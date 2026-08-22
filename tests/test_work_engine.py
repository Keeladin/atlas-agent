from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import (
    CapabilityBinding,
    CapabilityOutcome,
    ExecutionBudget,
    RetryPolicy,
)
from atlas_core.runtime_types import RuntimeBudget
from atlas_core.tools import ToolDescriptor, ToolGateway, ToolResult
from atlas_core.verification import VerificationResult, VerifierRegistry
from atlas_core.work import (
    UNAVAILABLE,
    CapabilityExecutionProfile,
    DeploymentInventory,
    ImplementationResolver,
    WorkEngine,
    WorkError,
    build_work_runtime,
)
from tests.work_helpers import engine_run_with_confirmation


WORK_ROOT = Path(__file__).resolve().parents[1] / "atlas_core" / "work"
ENGINE_FILES = (
    WORK_ROOT / "engine.py",
    WORK_ROOT / "lifecycle.py",
    WORK_ROOT / "execution.py",
    WORK_ROOT / "finish.py",
    WORK_ROOT / "model.py",
    WORK_ROOT / "confirmation.py",
)
RUNTIME_SOURCE = (WORK_ROOT / "runtime.py").read_text(encoding="utf-8")
ENGINE_FORBIDDEN = (
    "TaskRuntime",
    "CapabilityRegistry",
    "TaskPlanner",
    "RuntimeFrame",
    "atlas_core.planner",
    "atlas_core.bootstrap",
    "atlas_core.chat",
    "work_surfaces",
)


def _pass_handler(request):
    return CapabilityOutcome(
        "pass",
        output={"capability": request.capability_id, "work_id": request.work_id},
        receipt={"ok": True},
        claims=(
            {
                "kind": "executed",
                "subject": request.capability_id,
                "value": True,
            },
        ),
    )


def _binding(capability_id: str) -> CapabilityBinding:
    return CapabilityBinding(capability_id, "internal", "record", "1")


def _profile(capability_id: str, **overrides) -> CapabilityExecutionProfile:
    payload = dict(
        capability_id=capability_id,
        implementation=_binding(capability_id),
        verifier_id="core.nonempty",
        executor_kind="deterministic",
    )
    payload.update(overrides)
    return CapabilityExecutionProfile(**payload)


class WorkEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas-work.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _runtime(self, inventory: DeploymentInventory, gateway=None, **kwargs):
        return build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            tool_gateway=gateway,
            **kwargs,
        )

    def _engine(self, runtime, *, verifiers=None) -> WorkEngine:
        return WorkEngine(
            store=runtime._engine.store,
            tools=runtime._tool_gateway,
            verifiers=verifiers,
            model_consumer=runtime._engine.model_consumer,
        )

    def _resolve(self, runtime, work_id, inventory=None, gateway=None):
        contract = runtime.contract(work_id)
        report = ImplementationResolver().resolve(
            contract,
            inventory if inventory is not None else runtime._profiles,
            gateway if gateway is not None else runtime._tool_gateway,
        )
        return contract, report

    def test_source_does_not_use_legacy_engine_topology(self) -> None:
        for path in ENGINE_FILES:
            source = path.read_text(encoding="utf-8")
            for token in ENGINE_FORBIDDEN:
                with self.subTest(file=path.name, token=token):
                    self.assertNotIn(token, source)
            self.assertNotIn("self.capabilities.get", source)
            self.assertNotIn("tools.manifest(", source)
            self.assertNotIn("self.tools.manifest", source)
        model_source = (WORK_ROOT / "model.py").read_text(encoding="utf-8")
        self.assertNotIn("ToolGateway", model_source)
        self.assertNotIn("tools.invoke", model_source)

    def test_work_runtime_run_uses_work_engine(self) -> None:
        self.assertIn("from .engine import WorkEngine", RUNTIME_SOURCE)
        self.assertIn("self._engine.run(", RUNTIME_SOURCE)
        self.assertNotIn("TaskRuntime", RUNTIME_SOURCE)
        self.assertNotIn("CapabilityRegistry", RUNTIME_SOURCE)
        self.assertNotIn("work_surfaces", RUNTIME_SOURCE)

    def test_deterministic_handler_completes_with_receipt_and_evidence(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("automation.workflow.create"), _pass_handler)
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        contract, report = self._resolve(runtime, work_id)
        result = engine_run_with_confirmation(
            runtime, self._engine(runtime), contract, report
        )
        store = runtime._engine.store
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason, "work reached terminal state")
        self.assertGreaterEqual(result.executions, 1)
        executions = store.list_executions(work_id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].status, "pass")
        self.assertEqual(executions[0].attempt, 1)
        self.assertEqual(executions[0].capability_version, "1.0.0")
        kinds = {item.kind for item in store.list_artifacts(work_id)}
        self.assertIn("execution_receipt", kinds)
        self.assertIn("verification_result", kinds)
        self.assertTrue(store.list_claims(work_id))
        self.assertEqual(store.list_criteria(work_id)[0].status, "accepted")
        self.assertTrue(store.context_manifest_for_execution(executions[0].id))
        self.assertEqual(runtime.get(work_id).status, "completed")

    def test_handler_receives_required_surface_and_cannot_use_bait(self) -> None:
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        gateway.register(
            ToolDescriptor(id="bait.tool", description="Bait"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        seen: dict[str, object] = {}

        def handler(request):
            self.assertIsNotNone(request.surface)
            self.assertTrue(request.work_id)
            seen["surface"] = request.surface
            bait = request.surface.invoke("bait.tool", {})
            self.assertFalse(bait.ok)
            result = request.surface.invoke("mail.deliver", {"to": "ops@example.invalid"})
            return CapabilityOutcome(
                "pass" if result.ok else "fail",
                output=result.output,
                receipt=result.receipt,
                error=result.error,
            )

        inventory = DeploymentInventory()
        inventory.register(
            _profile("communication.email.send", tools=("mail.deliver",)),
            handler,
        )
        runtime = self._runtime(inventory, gateway)
        work_id = runtime.accept(
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
        )
        gateway.register(
            ToolDescriptor(id="late.tool", description="After accept"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        contract, report = self._resolve(runtime, work_id)
        result = engine_run_with_confirmation(
            runtime, self._engine(runtime), contract, report
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(seen["surface"].allowed_tools, frozenset({"mail.deliver@1.0.0"}))

    def test_unarmed_returns_unavailable_with_zero_executions(self) -> None:
        from atlas_core.work import UnavailableWork

        runtime = build_work_runtime(db_path=self.db)
        with self.assertRaises(UnavailableWork) as ctx:
            runtime.accept(
                TaskBrief(
                    objective="Create automation",
                    capabilities=("automation.workflow.create",),
                    required_authority="execute_external",
                    expected_effect="Create an automation workflow",
                ),
                "execute_external",
            )
        self.assertEqual(ctx.exception.result.status, "unavailable")
        self.assertEqual(runtime.store.list_work(), ())

    def test_mismatch_fails_that_step_and_still_runs_the_other(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("knowledge.search"), _pass_handler)
        inventory.register(
            _profile(
                "knowledge.answer",
                requires_artifact_kinds=("knowledge_search_results",),
                output_kind="grounded_answer",
            ),
            _pass_handler,
        )
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search then answer",
                capabilities=("knowledge.search", "knowledge.answer"),
                required_authority="read",
                expected_effect="A grounded local answer",
            ),
            "read",
        )
        drifted = DeploymentInventory()
        drifted.register(_profile("knowledge.search"), _pass_handler)
        contract, report = self._resolve(runtime, work_id, inventory=drifted)
        self.assertFalse(report.unarmed)
        self.assertEqual(
            tuple(item.capability_id for item in report.mismatches),
            ("knowledge.answer",),
        )
        result = self._engine(runtime).run(contract, report)
        store = runtime._engine.store
        by_capability = {item.capability: item for item in store.list_executions(work_id)}
        self.assertIn("knowledge.search", by_capability)
        self.assertEqual(by_capability["knowledge.search"].status, "pass")
        self.assertEqual(by_capability["knowledge.answer"].status, "fail")
        self.assertIn("resolve mismatch", by_capability["knowledge.answer"].error or "")
        self.assertEqual(result.status, "failed")

    def test_model_executor_fails_closed_as_not_implemented(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="reasoning.general",
                executor_kind="model",
                verifier_id="core.nonempty",
            )
        )
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Explain the request",
                capabilities=("reasoning.general",),
                required_authority="interpret",
                expected_effect="A bounded explanation",
            ),
            "interpret",
        )
        contract, report = self._resolve(runtime, work_id)
        result = self._engine(runtime).run(contract, report)
        executions = runtime._engine.store.list_executions(work_id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].status, "fail")
        self.assertEqual(executions[0].error, "executor not implemented")
        self.assertEqual(result.status, "failed")

    def test_human_pauses_before_execution_row_then_completes_on_approval(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                executor_kind="human",
                verification_required=False,
            )
        )
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        contract, report = self._resolve(runtime, work_id)
        engine = self._engine(runtime)
        store = runtime._engine.store
        first = engine.run(contract, report)
        self.assertEqual(first.status, "waiting")
        self.assertEqual(store.list_executions(work_id), ())
        self.assertEqual(store.list_steps(work_id)[0].status, "blocked")
        approvals = store.list_approvals(work_id, status="pending")
        self.assertEqual(len(approvals), 1)
        store.decide_approval(approvals[0].id, status="approved", note="do it")
        second = engine.run(contract, report)
        self.assertEqual(second.status, "waiting")
        self.assertEqual(store.list_executions(work_id), ())
        pending = runtime.list_pending_confirmations(work_id)
        self.assertEqual(len(pending), 1)
        runtime.confirm_payload(pending[0].id)
        third = engine.run(contract, report)
        self.assertEqual(third.status, "completed")
        executions = store.list_executions(work_id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].provider, "human")
        self.assertEqual(executions[0].status, "pass")

    def test_denied_approval_fails_the_step_without_an_execution_row(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                executor_kind="human",
                verification_required=False,
            )
        )
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        contract, report = self._resolve(runtime, work_id)
        engine = self._engine(runtime)
        store = runtime._engine.store
        engine.run(contract, report)
        approval = store.list_approvals(work_id, status="pending")[0]
        store.decide_approval(approval.id, status="denied", note="no")
        result = engine.run(contract, report)
        self.assertEqual(result.status, "failed")
        self.assertEqual(store.list_executions(work_id), ())
        self.assertEqual(store.list_steps(work_id)[0].status, "failed")

    def test_foreign_step_fails_the_work_without_executing_the_extra_id(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("automation.workflow.create"), _pass_handler)
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        runtime._engine.store.add_step(
            work_id,
            description="Foreign step",
            capability="reasoning.general",
            capability_version="9.9.9",
        )
        contract, report = self._resolve(runtime, work_id)
        result = self._engine(runtime).run(contract, report)
        self.assertEqual(result.status, "failed")
        self.assertIn("do not match the contract", result.reason)
        capabilities = {
            item.capability for item in runtime._engine.store.list_executions(work_id)
        }
        self.assertNotIn("reasoning.general", capabilities)
        self.assertEqual(runtime.get(work_id).status, "failed")

    def test_retry_is_a_new_append_only_execution_row(self) -> None:
        calls = {"n": 0}

        def handler(_request):
            calls["n"] += 1
            if calls["n"] == 1:
                return CapabilityOutcome("rework", output={"n": 1}, error="again")
            return CapabilityOutcome(
                "pass", output={"n": 2}, receipt={"ok": True}
            )

        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                "knowledge.search",
                budget=ExecutionBudget(max_attempts=3),
                retry_policy=RetryPolicy(retry_on=("rework", "abstain")),
            ),
            handler,
        )
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
        )
        contract, report = self._resolve(runtime, work_id)
        result = self._engine(runtime).run(contract, report)
        executions = runtime._engine.store.list_executions(work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(executions), 2)
        self.assertEqual(executions[0].status, "rework")
        self.assertEqual(executions[0].attempt, 1)
        self.assertEqual(executions[1].status, "pass")
        self.assertEqual(executions[1].attempt, 2)
        self.assertEqual(executions[0].error, "again")

    def test_non_idempotent_side_effect_is_not_retried(self) -> None:
        calls = {"n": 0}

        def handler(_request):
            calls["n"] += 1
            return CapabilityOutcome(
                "pass",
                output={"sent": True},
                receipt={"ok": True},
            )

        verifiers = VerifierRegistry()
        verifiers.register(
            "mail.verify",
            lambda spec, output, context: VerificationResult(
                "rework", "delivery state ambiguous"
            ),
        )
        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                "communication.email.send",
                verifier_id="mail.verify",
                side_effects=("external_email",),
                idempotent=False,
                budget=ExecutionBudget(max_attempts=3),
            ),
            handler,
        )
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
        )
        contract, report = self._resolve(runtime, work_id)
        result = engine_run_with_confirmation(
            runtime, self._engine(runtime, verifiers=verifiers), contract, report
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(calls["n"], 1)
        self.assertEqual(len(runtime._engine.store.list_executions(work_id)), 1)
        names = {event.name for event in runtime._engine.store.list_events(work_id)}
        self.assertIn("retry.blocked", names)

    def test_side_effect_without_receipt_fails(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                "communication.email.send",
                side_effects=("external_email",),
            ),
            lambda _request: CapabilityOutcome("pass", output={"sent": True}),
        )
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
        )
        contract, report = self._resolve(runtime, work_id)
        result = engine_run_with_confirmation(
            runtime, self._engine(runtime), contract, report
        )
        execution = runtime._engine.store.list_executions(work_id)[0]
        self.assertEqual(result.status, "failed")
        self.assertEqual(execution.status, "fail")
        self.assertIn("receipt", execution.error or "")

    def test_invocation_input_is_the_request_payload_not_the_brief(self) -> None:
        seen: dict[str, object] = {}

        def handler(request):
            seen["invocation_input"] = request.context.get("invocation_input")
            seen["manifests"] = runtime._engine.store.list_context_manifests(
                request.work_id
            )
            return _pass_handler(request)

        inventory = DeploymentInventory()
        inventory.register(_profile("knowledge.search"), handler)
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
            inputs={"knowledge.search": {"query": "atlas", "limit": 4}},
        )
        contract, report = self._resolve(runtime, work_id)
        result = self._engine(runtime).run(contract, report)
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            seen["invocation_input"], {"query": "atlas", "limit": 4}
        )
        self.assertEqual(len(seen["manifests"]), 1)

    def test_kind_match_dependency_output_is_visible_to_the_later_handler(self) -> None:
        seen: dict[str, object] = {}

        def search_handler(request):
            return CapabilityOutcome(
                "pass",
                output={"hits": ["a"]},
                output_kind="knowledge_search_results",
                receipt={"ok": True},
            )

        def answer_handler(request):
            seen["dependency_ids"] = request.dependency_artifact_ids
            seen["direct_ids"] = request.direct_input_artifact_ids
            return _pass_handler(request)

        inventory = DeploymentInventory()
        inventory.register(
            _profile("knowledge.search", output_kind="knowledge_search_results"),
            search_handler,
        )
        inventory.register(
            _profile(
                "knowledge.answer",
                requires_artifact_kinds=("knowledge_search_results",),
                output_kind="grounded_answer",
            ),
            answer_handler,
        )
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search then answer",
                capabilities=("knowledge.search", "knowledge.answer"),
                required_authority="read",
                expected_effect="A grounded local answer",
            ),
            "read",
        )
        contract, report = self._resolve(runtime, work_id)
        result = self._engine(runtime).run(contract, report)
        self.assertEqual(result.status, "completed")
        self.assertTrue(seen["dependency_ids"])
        self.assertEqual(seen["direct_ids"], ())

    def test_work_budget_is_taken_from_the_contract(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            _profile("knowledge.search", output_kind="knowledge_search_results"),
            _pass_handler,
        )
        inventory.register(
            _profile(
                "knowledge.answer",
                requires_artifact_kinds=("knowledge_search_results",),
            ),
            _pass_handler,
        )
        runtime = self._runtime(
            inventory, budget=RuntimeBudget(max_executions=1, max_cycles=10)
        )
        work_id = runtime.accept(
            TaskBrief(
                objective="Search then answer",
                capabilities=("knowledge.search", "knowledge.answer"),
                required_authority="read",
                expected_effect="A grounded local answer",
            ),
            "read",
        )
        contract, report = self._resolve(runtime, work_id)
        self.assertEqual(contract.work_budget.max_executions, 1)
        result = self._engine(runtime).run(contract, report)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "work execution budget exhausted")
        self.assertEqual(len(runtime._engine.store.list_executions(work_id)), 1)

    def test_recover_fail_closes_non_idempotent_running_execution(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                "communication.email.send",
                side_effects=("external_email",),
                idempotent=False,
            ),
            _pass_handler,
        )
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
        )
        store = runtime._engine.store
        step = store.list_steps(work_id)[0]
        store.set_work_status(work_id, "active")
        store.begin_execution(
            work_id,
            step_id=step.id,
            capability="communication.email.send",
            capability_version="1.0.0",
        )
        contract, report = self._resolve(runtime, work_id)
        recovered = self._engine(runtime).recover(contract, report)
        self.assertEqual(recovered.failed_closed, 1)
        self.assertEqual(recovered.recovered, 0)
        self.assertEqual(recovered.status, "failed")
        self.assertEqual(store.list_executions(work_id)[0].status, "fail")

    def test_recover_retries_idempotent_interrupted_execution(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("knowledge.search"), _pass_handler)
        runtime = self._runtime(inventory)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
        )
        store = runtime._engine.store
        step = store.list_steps(work_id)[0]
        store.set_work_status(work_id, "active")
        store.begin_execution(
            work_id,
            step_id=step.id,
            capability="knowledge.search",
            capability_version="1.0.0",
        )
        contract, report = self._resolve(runtime, work_id)
        recovered = self._engine(runtime).recover(contract, report)
        self.assertEqual(recovered.recovered, 1)
        self.assertEqual(recovered.failed_closed, 0)
        self.assertEqual(store.list_executions(work_id)[0].status, "abstain")
        self.assertEqual(store.get_step(step.id).status, "pending")

    def test_mismatched_report_is_rejected(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("knowledge.search"), _pass_handler)
        runtime = self._runtime(inventory)
        first = runtime.accept(
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
        )
        second = runtime.accept(
            TaskBrief(
                objective="Search again",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
        )
        contract, _report = self._resolve(runtime, first)
        _other, other_report = self._resolve(runtime, second)
        with self.assertRaises(WorkError):
            self._engine(runtime).run(contract, other_report)

    def test_context_pack_allowed_tools_are_the_pin(self) -> None:
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        gateway.register(
            ToolDescriptor(id="bait.tool", description="Bait"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        seen: dict[str, object] = {}

        def handler(request):
            seen["allowed_tools"] = request.context["capability_contract"]["allowed_tools"]
            return _pass_handler(request)

        inventory = DeploymentInventory()
        inventory.register(
            _profile("communication.email.send", tools=("mail.deliver",)),
            handler,
        )
        runtime = self._runtime(inventory, gateway)
        work_id = runtime.accept(
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
        )
        contract, report = self._resolve(runtime, work_id)
        engine_run_with_confirmation(
            runtime, self._engine(runtime), contract, report
        )
        self.assertEqual(seen["allowed_tools"], ["mail.deliver@1.0.0"])
        self.assertNotIn("bait.tool", seen["allowed_tools"])
