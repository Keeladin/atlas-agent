from __future__ import annotations

"""WorkRuntime.run vs WorkEngine: the composition root matches the engine.

There is one execution engine. These cases prove WorkRuntime.run and a
direct WorkEngine.run produce the same durable snapshot.
"""

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
from atlas_core.verification import VerificationResult
from atlas_core.work import (
    UNAVAILABLE,
    CapabilityExecutionProfile,
    DeploymentInventory,
    ImplementationResolver,
    WorkEngine,
    build_work_runtime,
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


def _pass(_request):
    return CapabilityOutcome(
        "pass",
        output={"ok": True},
        receipt={"ok": True},
        claims=({"kind": "executed", "subject": "ok", "value": True},),
    )


def _semantic_snapshot(store, work_id, result) -> dict:
    steps = store.list_steps(work_id)
    executions = store.list_executions(work_id)
    artifacts = store.list_artifacts(work_id)
    return {
        "result_status": result.status,
        "result_reason": result.reason,
        "result_executions": result.executions,
        "work_status": store.get_work(work_id).status,
        "steps": tuple((step.capability, step.status) for step in steps),
        "executions": tuple(
            (
                item.capability,
                item.status,
                item.attempt,
                item.error,
                item.provider,
                bool(item.receipt),
                bool(item.verifier_artifact_id),
                bool(item.output_artifact_ids),
            )
            for item in executions
        ),
        "artifact_kinds": tuple(sorted({item.kind for item in artifacts})),
        "criteria": tuple(item.status for item in store.list_criteria(work_id)),
        "claim_kinds": tuple(item.kind for item in store.list_claims(work_id)),
        "approvals": tuple(
            (item.status, item.required_authority)
            for item in store.list_approvals(work_id)
        ),
        "event_names": tuple(item.name for item in store.list_events(work_id)),
        "manifests": len(store.list_context_manifests(work_id)),
        "executed_capabilities": tuple(
            item.capability for item in executions if item.status == "pass"
        ),
    }


class WorkEngineConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas-work.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _pair(
        self,
        inventory: DeploymentInventory,
        brief: TaskBrief,
        authority: str,
        *,
        inputs=None,
        gateway=None,
        budget=None,
        prepare=None,
    ):
        runtime = build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            tool_gateway=gateway,
            budget=budget,
        )
        left = runtime.accept(brief, authority, inputs=inputs)
        right = runtime.accept(brief, authority, inputs=inputs)
        if prepare is not None:
            prepare(runtime, left, right)
        runtime_result = runtime.run(left)
        contract = runtime.contract(right)
        report = ImplementationResolver().resolve(
            contract, runtime._profiles, runtime._tool_gateway
        )
        engine = WorkEngine(
            store=runtime._engine.store,
            tools=runtime._tool_gateway,
            verifiers=runtime._engine.verifiers,
            event_bus=runtime._engine.event_bus,
            outcome_gate=runtime._engine.outcome_gate,
        )
        work_engine_result = engine.run(contract, report)
        store = runtime._engine.store
        return (
            _semantic_snapshot(store, left, runtime_result),
            _semantic_snapshot(store, right, work_engine_result),
            runtime,
            left,
            right,
        )

    def test_unarmed_is_unavailable_on_runtime_and_engine(self) -> None:
        left, right, _runtime, _left_id, _right_id = self._pair(
            DeploymentInventory(),
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        self.assertEqual(left, right)
        self.assertEqual(left["result_reason"], UNAVAILABLE)
        self.assertEqual(left["result_executions"], 0)
        self.assertEqual(left["work_status"], "planned")

    def test_deterministic_pass_matches(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("automation.workflow.create"), _pass)
        left, right, *_ = self._pair(
            inventory,
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        self.assertEqual(left, right)
        self.assertEqual(left["result_status"], "completed")
        self.assertEqual(left["executions"][0][1], "pass")
        self.assertIn("execution_receipt", left["artifact_kinds"])
        self.assertIn("verification_result", left["artifact_kinds"])

    def test_tool_surface_invoke_matches(self) -> None:
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        gateway.register(
            ToolDescriptor(id="bait.tool", description="Bait"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        seen: dict[str, tuple] = {}

        def handler(request):
            bait = request.surface.invoke("bait.tool", {})
            delivered = request.surface.invoke(
                "mail.deliver", {"to": "ops@example.invalid"}
            )
            seen[request.work_id] = (
                request.surface.allowed_tools,
                bait.ok,
                delivered.ok,
            )
            return CapabilityOutcome(
                "pass" if delivered.ok else "fail",
                output={"sent": delivered.ok},
                receipt=delivered.receipt,
                error=delivered.error,
            )

        inventory = DeploymentInventory()
        inventory.register(
            _profile("communication.email.send", tools=("mail.deliver",)),
            handler,
        )
        left, right, _runtime, left_id, right_id = self._pair(
            inventory,
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
            gateway=gateway,
        )
        self.assertEqual(left, right)
        self.assertEqual(left["result_status"], "completed")
        self.assertEqual(seen[left_id], seen[right_id])
        self.assertEqual(seen[right_id][0], frozenset({"mail.deliver@1.0.0"}))
        self.assertFalse(seen[right_id][1])

    def test_retry_append_only_matches(self) -> None:
        attempts: dict[str, int] = {}

        def handler(request):
            key = request.work_id
            attempts[key] = attempts.get(key, 0) + 1
            if attempts[key] == 1:
                return CapabilityOutcome("rework", output={"n": 1}, error="again")
            return CapabilityOutcome("pass", output={"n": 2}, receipt={"ok": True})

        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                "knowledge.search",
                budget=ExecutionBudget(max_attempts=3),
                retry_policy=RetryPolicy(retry_on=("rework", "abstain")),
            ),
            handler,
        )
        left, right, *_ = self._pair(
            inventory,
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
        )
        self.assertEqual(left, right)
        self.assertEqual(left["result_status"], "completed")
        self.assertEqual(
            [(status, attempt) for _cap, status, attempt, *_rest in left["executions"]],
            [("rework", 1), ("pass", 2)],
        )

    def test_side_effect_without_receipt_matches(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                "communication.email.send",
                side_effects=("external_email",),
            ),
            lambda _request: CapabilityOutcome("pass", output={"sent": True}),
        )
        left, right, *_ = self._pair(
            inventory,
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
        )
        self.assertEqual(left, right)
        self.assertEqual(left["result_status"], "failed")
        self.assertEqual(left["executions"][0][1], "fail")

    def test_non_idempotent_side_effect_is_not_retried_on_runtime_or_engine(self) -> None:
        calls: dict[str, int] = {}

        def handler(request):
            key = request.work_id
            calls[key] = calls.get(key, 0) + 1
            return CapabilityOutcome(
                "pass",
                output={"sent": True},
                receipt={"ok": True},
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
        runtime = build_work_runtime(db_path=self.db, profiles=inventory)
        runtime._engine.verifiers.register(
            "mail.verify",
            lambda spec, output, context: VerificationResult(
                "rework", "delivery state ambiguous"
            ),
        )
        brief = TaskBrief(
            objective="Send the report",
            capabilities=("communication.email.send",),
            required_authority="communicate",
            expected_effect="external communication",
        )
        left_id = runtime.accept(brief, "communicate")
        right_id = runtime.accept(brief, "communicate")
        left_result = runtime.run(left_id)
        contract = runtime.contract(right_id)
        report = ImplementationResolver().resolve(
            contract, runtime._profiles, runtime._tool_gateway
        )
        right_result = WorkEngine(
            store=runtime._engine.store,
            tools=runtime._tool_gateway,
            verifiers=runtime._engine.verifiers,
            event_bus=runtime._engine.event_bus,
            outcome_gate=runtime._engine.outcome_gate,
        ).run(contract, report)
        store = runtime._engine.store
        left = _semantic_snapshot(store, left_id, left_result)
        right = _semantic_snapshot(store, right_id, right_result)
        self.assertEqual(left, right)
        self.assertEqual(left["result_status"], "failed")
        self.assertEqual(left["executions"][0][1], "rework")
        self.assertIn("retry.blocked", left["event_names"])
        self.assertEqual(calls[left_id], 1)
        self.assertEqual(calls[right_id], 1)

    def test_kind_match_dependency_matches(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            _profile("knowledge.search", output_kind="knowledge_search_results"),
            lambda _request: CapabilityOutcome(
                "pass",
                output={"hits": ["a"]},
                output_kind="knowledge_search_results",
                receipt={"ok": True},
            ),
        )
        inventory.register(
            _profile(
                "knowledge.answer",
                requires_artifact_kinds=("knowledge_search_results",),
                output_kind="grounded_answer",
            ),
            _pass,
        )
        left, right, *_ = self._pair(
            inventory,
            TaskBrief(
                objective="Search then answer",
                capabilities=("knowledge.search", "knowledge.answer"),
                required_authority="read",
                expected_effect="A grounded local answer",
            ),
            "read",
        )
        self.assertEqual(left, right)
        self.assertEqual(left["result_status"], "completed")
        self.assertEqual(
            [cap for cap, _status in left["steps"]],
            ["knowledge.search", "knowledge.answer"],
        )

    def test_request_payload_matches(self) -> None:
        seen: dict[str, object] = {}

        def handler(request):
            seen[request.work_id] = request.context.get(
                "invocation_input"
            )
            return _pass(request)

        inventory = DeploymentInventory()
        inventory.register(_profile("knowledge.search"), handler)
        left, right, _runtime, left_id, right_id = self._pair(
            inventory,
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
            inputs={"knowledge.search": {"query": "atlas", "limit": 4}},
        )
        self.assertEqual(left, right)
        self.assertEqual(seen[left_id], {"query": "atlas", "limit": 4})
        self.assertEqual(seen[right_id], {"query": "atlas", "limit": 4})

    def test_mismatch_fails_the_drifted_step_on_runtime_and_engine(self) -> None:
        accept_inventory = DeploymentInventory()
        accept_inventory.register(_profile("knowledge.search"), _pass)
        accept_inventory.register(
            _profile(
                "knowledge.answer",
                requires_artifact_kinds=("knowledge_search_results",),
                output_kind="grounded_answer",
            ),
            _pass,
        )
        runtime = build_work_runtime(db_path=self.db, profiles=accept_inventory)
        brief = TaskBrief(
            objective="Search then answer",
            capabilities=("knowledge.search", "knowledge.answer"),
            required_authority="read",
            expected_effect="A grounded local answer",
        )
        left_id = runtime.accept(brief, "read")
        right_id = runtime.accept(brief, "read")
        drifted = DeploymentInventory()
        drifted.register(_profile("knowledge.search"), _pass)
        runtime._profiles = drifted
        left_result = runtime.run(left_id)
        contract = runtime.contract(right_id)
        report = ImplementationResolver().resolve(
            contract, drifted, runtime._tool_gateway
        )
        right_result = WorkEngine(
            store=runtime._engine.store,
            tools=runtime._tool_gateway,
            verifiers=runtime._engine.verifiers,
            event_bus=runtime._engine.event_bus,
            outcome_gate=runtime._engine.outcome_gate,
        ).run(contract, report)
        store = runtime._engine.store
        left = _semantic_snapshot(store, left_id, left_result)
        right = _semantic_snapshot(store, right_id, right_result)
        self.assertEqual(left, right)
        statuses = {cap: status for cap, status, *_rest in left["executions"]}
        errors = {cap: error for cap, _status, _attempt, error, *_rest in left["executions"]}
        self.assertEqual(statuses["knowledge.search"], "pass")
        self.assertEqual(statuses["knowledge.answer"], "fail")
        self.assertIn("resolve mismatch", errors["knowledge.answer"] or "")

    def test_human_authority_pause_matches(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                executor_kind="human",
                verification_required=False,
            )
        )
        left, right, *_ = self._pair(
            inventory,
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        self.assertEqual(left, right)
        self.assertEqual(left["result_status"], "waiting")
        self.assertEqual(left["result_executions"], 0)
        self.assertEqual(left["steps"][0][1], "blocked")
        self.assertEqual(left["approvals"][0][0], "pending")

    def test_work_budget_exhaustion_matches(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            _profile("knowledge.search", output_kind="knowledge_search_results"),
            _pass,
        )
        inventory.register(
            _profile(
                "knowledge.answer",
                requires_artifact_kinds=("knowledge_search_results",),
            ),
            _pass,
        )
        left, right, *_ = self._pair(
            inventory,
            TaskBrief(
                objective="Search then answer",
                capabilities=("knowledge.search", "knowledge.answer"),
                required_authority="read",
                expected_effect="A grounded local answer",
            ),
            "read",
            budget=RuntimeBudget(max_executions=1, max_cycles=10),
        )
        self.assertEqual(left, right)
        self.assertEqual(left["result_status"], "failed")
        self.assertEqual(left["result_reason"], "work execution budget exhausted")
        self.assertEqual(left["result_executions"], 1)

    def test_foreign_step_fails_on_runtime_and_engine(self) -> None:
        """A store-injected step that is not in the WorkContract fails closed.

        The extra id never runs. WorkRuntime.run matches WorkEngine.
        """

        inventory = DeploymentInventory()
        inventory.register(_profile("automation.workflow.create"), _pass)
        inventory.register(_profile("knowledge.search"), _pass)

        def prepare(runtime, left, right):
            for work_id in (left, right):
                runtime._engine.store.add_step(
                    work_id,
                    description="Foreign step",
                    capability="knowledge.search",
                    capability_version="1.0.0",
                )

        left, right, *_ = self._pair(
            inventory,
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
            prepare=prepare,
        )
        self.assertEqual(left, right)
        self.assertEqual(left["result_status"], "failed")
        self.assertIn("do not match the contract", left["result_reason"])
        self.assertNotIn("knowledge.search", left["executed_capabilities"])
        self.assertNotIn("knowledge.search", right["executed_capabilities"])

    def test_model_kind_fails_closed_on_runtime_and_engine(self) -> None:
        """Model pins fail closed until the Work model path exists.

        WorkRuntime.run matches WorkEngine: executor not implemented.
        """
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="reasoning.general",
                executor_kind="model",
                verifier_id="core.nonempty",
            )
        )
        left, right, *_ = self._pair(
            inventory,
            TaskBrief(
                objective="Explain the request",
                capabilities=("reasoning.general",),
                required_authority="interpret",
                expected_effect="A bounded explanation",
            ),
            "interpret",
        )
        self.assertEqual(left, right)
        self.assertEqual(left["executions"][0][1], "fail")
        self.assertEqual(left["executions"][0][3], "executor not implemented")
        self.assertEqual(right["executions"][0][3], "executor not implemented")
