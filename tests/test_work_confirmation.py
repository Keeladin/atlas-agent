from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import CapabilityOutcome, ExecutionBudget
from atlas_core.providers import ModelResponse, ModelRouter, ProviderRegistry, ProviderSpec
from atlas_core.tools import ToolDescriptor, ToolGateway, ToolResult
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    confirmation_digest,
    confirmation_document,
    confirmation_summary,
    build_work_runtime,
)
from tests.work_helpers import run_with_confirmation


def _pass_handler(request):
    return CapabilityOutcome(
        "pass",
        output={"capability": request.capability_id},
        receipt={"ok": True},
    )


class FakeProvider:
    def __init__(self, spec: ProviderSpec, *, text: str = "created") -> None:
        self.spec = spec
        self.text = text
        self.calls: list = []

    def generate(self, request):
        self.calls.append(request)
        return ModelResponse(
            self.text,
            self.spec.key,
            self.spec.model,
            raw={},
            metrics={"input_tokens": 4, "output_tokens": 2},
        )


class WorkConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas-work.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _email_runtime(self, handler=None, *, gateway=None):
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                tools=("mail.deliver",) if gateway is not None else (),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                side_effects=("external_email",),
            ),
            handler or _pass_handler,
        )
        return build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            tool_gateway=gateway,
        )

    def _accept_email(self, runtime, *, inputs=None):
        return runtime.accept(
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
            inputs=inputs,
        )

    def test_standing_authority_still_pauses_on_required_confirmation(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(runtime)
        result = runtime.run(work_id)
        self.assertEqual(result.status, "waiting")
        self.assertEqual(runtime.store.list_approvals(work_id), ())
        pending = runtime.list_pending_confirmations(work_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].capability_id, "communication.email.send")
        self.assertEqual(runtime.store.list_steps(work_id)[0].status, "blocked")

    def test_no_execution_row_or_attempt_before_confirmation(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(runtime)
        runtime.run(work_id)
        self.assertEqual(runtime.store.list_executions(work_id), ())
        self.assertEqual(runtime.get(work_id).status, "waiting")

    def test_confirmed_exact_payload_executes(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(
            runtime,
            inputs={
                "communication.email.send": {
                    "to": "ops@example.invalid",
                    "subject": "Weekly report",
                }
            },
        )
        first = runtime.run(work_id)
        self.assertEqual(first.status, "waiting")
        pending = runtime.list_pending_confirmations(work_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            pending[0].summary,
            "Atlas wants to send this email to ops@example.invalid with subject Weekly report",
        )
        confirmed = runtime.confirm_payload(pending[0].id)
        self.assertEqual(confirmed.status, "confirmed")
        second = runtime.run(work_id)
        self.assertEqual(second.status, "completed")
        executions = runtime.store.list_executions(work_id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].attempt, 1)
        self.assertEqual(executions[0].status, "pass")

    def test_changed_payload_invalidates_confirmation(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(
            runtime,
            inputs={
                "communication.email.send": {
                    "to": "ops@example.invalid",
                    "subject": "Weekly report",
                }
            },
        )
        runtime.run(work_id)
        original = runtime.list_pending_confirmations(work_id)[0]
        runtime.confirm_payload(original.id)
        step = runtime.store.list_steps(work_id)[0]
        artifact_id = step.input_artifact_ids[0]
        with sqlite3.connect(self.db) as db:
            db.execute(
                "UPDATE work_artifacts SET payload_json=? WHERE id=?",
                (
                    json.dumps(
                        {"to": "other@example.invalid", "subject": "Weekly report"}
                    ),
                    artifact_id,
                ),
            )
            db.commit()
        result = runtime.run(work_id)
        self.assertEqual(result.status, "waiting")
        self.assertEqual(runtime.store.list_executions(work_id), ())
        pending = runtime.list_pending_confirmations(work_id)
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0].payload_sha256, original.payload_sha256)
        self.assertIn("other@example.invalid", pending[0].summary)
        self.assertEqual(runtime.store.get_confirmation(original.id).status, "confirmed")

    def test_denied_payload_fails_closed(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(runtime)
        runtime.run(work_id)
        pending = runtime.list_pending_confirmations(work_id)[0]
        refused = runtime.deny_confirmation(pending.id)
        self.assertEqual(refused.status, "denied")
        result = runtime.run(work_id)
        self.assertEqual(result.status, "failed")
        self.assertEqual(runtime.store.list_executions(work_id), ())
        self.assertEqual(runtime.store.list_steps(work_id)[0].status, "failed")
        self.assertEqual(runtime.list_pending_confirmations(work_id), ())

    def test_cancelled_payload_can_rearm(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(runtime)
        runtime.run(work_id)
        first = runtime.list_pending_confirmations(work_id)[0]
        cancelled = runtime.cancel_confirmation(first.id)
        self.assertEqual(cancelled.status, "cancelled")
        second_run = runtime.run(work_id)
        self.assertEqual(second_run.status, "waiting")
        pending = runtime.list_pending_confirmations(work_id)
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0].id, first.id)
        self.assertEqual(pending[0].payload_sha256, first.payload_sha256)
        runtime.confirm_payload(pending[0].id)
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")

    def test_same_confirmed_payload_does_not_request_duplicate(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(runtime)
        runtime.run(work_id)
        runtime.run(work_id)
        pending = runtime.list_pending_confirmations(work_id)
        self.assertEqual(len(pending), 1)
        runtime.confirm_payload(pending[0].id)
        runtime.run(work_id)
        records = runtime.store.list_confirmations(work_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "confirmed")
        self.assertEqual(len(runtime.store.list_executions(work_id)), 1)

    def test_confirmation_survives_reload(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(runtime)
        runtime.run(work_id)
        pending = runtime.list_pending_confirmations(work_id)[0]
        runtime.confirm_payload(pending.id)
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                side_effects=("external_email",),
            ),
            _pass_handler,
        )
        reloaded = build_work_runtime(db_path=self.db, profiles=inventory)
        recovered = reloaded.recover(work_id)
        self.assertEqual(recovered.recovered, 0)
        self.assertEqual(recovered.failed_closed, 0)
        result = reloaded.run(work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(reloaded.store.list_executions(work_id)), 1)
        self.assertEqual(
            reloaded.store.list_confirmations(work_id)[0].status, "confirmed"
        )

    def test_waiting_confirmation_is_not_interrupted_work(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(runtime)
        runtime.run(work_id)
        recovered = runtime.recover(work_id)
        self.assertEqual(recovered.recovered, 0)
        self.assertEqual(recovered.failed_closed, 0)
        self.assertEqual(runtime.get(work_id).status, "waiting")
        self.assertEqual(runtime.store.list_executions(work_id), ())
        self.assertEqual(runtime.resume(work_id), 0)
        self.assertEqual(runtime.store.list_steps(work_id)[0].status, "blocked")

    def test_confirmation_is_separate_from_authority_approval(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(runtime)
        runtime.run(work_id)
        self.assertEqual(runtime.store.list_approvals(work_id), ())
        pending = runtime.list_pending_confirmations(work_id)
        self.assertEqual(len(pending), 1)
        with self.assertRaises(Exception):
            runtime.approve(pending[0].id)
        with self.assertRaises(Exception):
            runtime.deny(pending[0].id)
        self.assertEqual(
            runtime.store.get_confirmation(pending[0].id).status, "pending"
        )

    def test_tool_path_does_not_invoke_before_confirmation(self) -> None:
        gateway = ToolGateway()
        calls: list[tuple[str, object]] = []

        def deliver(arguments):
            calls.append(("mail.deliver", arguments))
            return ToolResult(True, output=arguments, receipt={"ok": True})

        gateway.register(ToolDescriptor(id="mail.deliver", description="Deliver"), deliver)

        def handler(request):
            result = request.surface.invoke("mail.deliver", {"to": "ops@example.invalid"})
            return CapabilityOutcome(
                "pass" if result.ok else "fail",
                output=result.output,
                receipt=result.receipt,
                error=result.error,
            )

        runtime = self._email_runtime(handler, gateway=gateway)
        work_id = self._accept_email(runtime)
        runtime.run(work_id)
        self.assertEqual(calls, [])
        self.assertEqual(runtime.store.list_executions(work_id), ())
        runtime.confirm_payload(runtime.list_pending_confirmations(work_id)[0].id)
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(calls), 1)

    def test_model_path_pauses_before_provider_call(self) -> None:
        provider = FakeProvider(
            ProviderSpec(
                key="local:primary",
                model="model-local:primary",
                provider_kind="fake",
                capabilities={"automation.workflow.create": 0.9},
                local=True,
                enabled=True,
                max_context_chars=128_000,
            )
        )
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                executor_kind="model",
                verifier_id="core.nonempty",
                eligible_providers=("local:primary",),
            )
        )
        registry = ProviderRegistry()
        registry.register(provider)
        runtime = build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            model_router=ModelRouter(registry),
        )
        work_id = runtime.accept(
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
            inputs={
                "automation.workflow.create": {
                    "name": "inbox-triage",
                    "parameters": {"label": "ops"},
                }
            },
        )
        result = runtime.run(work_id)
        self.assertEqual(result.status, "waiting")
        self.assertEqual(provider.calls, [])
        self.assertEqual(runtime.store.list_executions(work_id), ())
        pending = runtime.list_pending_confirmations(work_id)[0]
        self.assertEqual(
            pending.summary,
            "Atlas wants to create workflow inbox-triage with these parameters",
        )
        runtime.confirm_payload(pending.id)
        completed = runtime.run(work_id)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(len(provider.calls), 1)

    def test_non_confirming_capabilities_are_unchanged(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.search",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _pass_handler,
        )
        runtime = build_work_runtime(db_path=self.db, profiles=inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
        )
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(runtime.list_pending_confirmations(work_id), ())
        self.assertEqual(runtime.store.list_confirmations(work_id), ())
        self.assertEqual(len(runtime.store.list_executions(work_id)), 1)

    def test_payload_hash_covers_pins_not_ambient_state(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(
            runtime,
            inputs={"communication.email.send": {"to": "ops@example.invalid"}},
        )
        runtime.run(work_id)
        step = runtime.store.list_steps(work_id)[0]
        pin = runtime.contract(work_id).capability("communication.email.send")
        first = confirmation_document(runtime.store, step, pin)
        runtime.store.set_work_status(work_id, "waiting")
        second = confirmation_document(runtime.store, step, pin)
        self.assertEqual(confirmation_digest(first), confirmation_digest(second))
        self.assertNotIn("status", first)
        self.assertNotIn("attempt", first)
        self.assertIn("capability_id", first)
        self.assertIn("invocation_input", first)
        self.assertEqual(first["invocation_input"]["to"], "ops@example.invalid")

    def test_workflow_execute_summary(self) -> None:
        self.assertEqual(
            confirmation_summary(
                "automation.workflow.execute",
                {"workflow_id": "wf-9", "parameters": {"dry_run": False}},
            ),
            "Atlas wants to execute workflow wf-9 with these parameters",
        )

    def test_helper_run_with_confirmation_completes_email(self) -> None:
        runtime = self._email_runtime()
        work_id = self._accept_email(runtime)
        result = run_with_confirmation(runtime, work_id)
        self.assertEqual(result.status, "completed")
