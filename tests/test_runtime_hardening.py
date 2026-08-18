from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from atlas_core.capabilities import (
    CapabilityOutcome,
    CapabilityRegistry,
    CapabilitySpec,
    ExecutionBudget,
)
from atlas_core.evals import EvalCase, EvalHarness, record_eval_report
from atlas_core.events import EventBus, RuntimeEvent
from atlas_core.planner import PlanError, TaskPlanner
from atlas_core.presentation import TaskPresenter
from atlas_core.providers import ModelResponse, ModelRouter, ProviderRegistry, ProviderScoreStore, ProviderSpec
from atlas_core.runtime import RuntimeBudget, TaskRuntime
from atlas_core.tasks import InvalidTransitionError, TaskStore
from atlas_core.tools import ToolGateway, ToolResult, ToolSpec
from atlas_core.verification import VerifierRegistry


class RuntimeHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tmp.name) / "atlas.db")
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, *, authority="read"):
        return self.store.create_task(
            objective="Harden execution truth",
            success_criteria=("The bounded action is accepted",),
            authority_scope=authority,
        )

    def test_runtime_schema_version_is_persisted(self):
        with self.store._db() as db:
            row = db.execute(
                "SELECT version FROM atlas_schema_meta WHERE component='task_runtime'"
            ).fetchone()
        self.assertEqual(int(row["version"]), 1)

    def test_accepted_criterion_requires_evidence(self):
        task = self.task()
        criterion = self.store.list_criteria(task.id)[0]
        with self.assertRaises(ValueError):
            self.store.set_criterion_status(criterion.id, "accepted")

    def test_tool_capability_cannot_bypass_side_effect_receipt_gate(self):
        gateway = ToolGateway()
        gateway.register(
            ToolSpec(
                "external.send",
                "send",
                required_authority="communicate",
                side_effects=("external_message",),
                idempotent=False,
            ),
            lambda arguments: ToolResult(True, output={"sent": True}),
        )
        spec, handler = gateway.capability("external.send")
        capabilities = CapabilityRegistry()
        capabilities.register(spec, handler)
        task = self.task(authority="communicate")
        self.store.add_step(
            task.id,
            description="Send once",
            capability="external.send",
            metadata={"accept_all_criteria": True},
        )
        result = TaskRuntime(store=self.store, capabilities=capabilities).run_until_blocked(task.id)
        self.assertEqual(result.status, "failed")
        execution = self.store.list_executions(task.id)[0]
        self.assertEqual(execution.status, "fail")
        self.assertFalse(execution.receipt.get("ok", True))

    def test_execution_record_exists_before_handler_runs(self):
        capabilities = CapabilityRegistry()
        observed = {"running": False}

        def handler(request):
            executions = self.store.list_executions(request.task_id, step_id=request.step_id)
            observed["running"] = bool(executions and executions[-1].status == "running")
            return CapabilityOutcome("pass", output={"ok": True})

        capabilities.register(
            CapabilitySpec(
                id="demo.safe",
                description="safe",
                executor_kind="deterministic",
                verifier_id="core.nonempty",
            ),
            handler,
        )
        task = self.task()
        self.store.add_step(
            task.id,
            description="Safe work",
            capability="demo.safe",
            metadata={"accept_all_criteria": True},
        )
        result = TaskRuntime(store=self.store, capabilities=capabilities).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertTrue(observed["running"])

    def test_unknown_capability_fails_durably_without_escaping_runtime(self):
        task = self.task()
        self.store.add_step(
            task.id,
            description="Unknown work",
            capability="missing.capability",
        )
        result = TaskRuntime(
            store=self.store,
            capabilities=CapabilityRegistry(),
        ).run_until_blocked(task.id)
        self.assertEqual(result.status, "failed")
        executions = self.store.list_executions(task.id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].status, "fail")
        self.assertIn("Unknown capability", executions[0].error)

    def test_human_gate_completes_after_one_explicit_approval(self):
        capabilities = CapabilityRegistry()
        capabilities.register(
            CapabilitySpec(
                id="human.confirm",
                description="human confirmation",
                executor_kind="human",
                required_authority="recommend",
                output_kind="human_decision",
                verifier_id=None,
                verification_required=False,
            )
        )
        task = self.task(authority="read")
        step = self.store.add_step(
            task.id,
            description="Confirm action",
            capability="human.confirm",
            metadata={"accept_all_criteria": True},
        )
        runtime = TaskRuntime(store=self.store, capabilities=capabilities)
        first = runtime.run_until_blocked(task.id)
        self.assertEqual(first.status, "waiting")
        pending = self.store.list_approvals(task.id, status="pending")
        self.assertEqual(len(pending), 1)
        self.store.decide_approval(pending[0].id, status="approved", note="approved")
        second = runtime.run_until_blocked(task.id)
        self.assertEqual(second.status, "completed")
        self.assertEqual(len(self.store.list_approvals(task.id)), 1)
        self.assertEqual(self.store.get_step(step.id).status, "pass")
        self.assertEqual(self.store.list_executions(task.id)[0].provider, "human")

    def test_interrupted_idempotent_execution_can_be_recovered_and_retried(self):
        capabilities = CapabilityRegistry()
        capabilities.register(
            CapabilitySpec(
                id="demo.retry",
                description="retryable",
                executor_kind="deterministic",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=3),
            ),
            lambda request: CapabilityOutcome("pass", output={"ok": True}),
        )
        task = self.task()
        step = self.store.add_step(
            task.id,
            description="Retryable",
            capability="demo.retry",
            metadata={"accept_all_criteria": True},
        )
        self.store.set_task_status(task.id, "active")
        interrupted = self.store.begin_execution(task.id, step_id=step.id, capability="demo.retry")
        runtime = TaskRuntime(store=self.store, capabilities=capabilities)
        recovery = runtime.recover_interrupted(task.id)
        self.assertEqual(recovery.recovered, 1)
        self.assertEqual(self.store.get_execution(interrupted.id).status, "abstain")
        result = runtime.run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.store.list_executions(task.id)), 2)

    def test_interrupted_non_idempotent_side_effect_fails_closed(self):
        capabilities = CapabilityRegistry()
        capabilities.register(
            CapabilitySpec(
                id="external.once",
                description="external once",
                executor_kind="tool",
                required_authority="communicate",
                side_effects=("external_change",),
                idempotent=False,
                verifier_id="core.receipt",
            ),
            lambda request: CapabilityOutcome("pass", output={"done": True}, receipt={"ok": True}),
        )
        task = self.task(authority="communicate")
        step = self.store.add_step(task.id, description="External", capability="external.once")
        self.store.set_task_status(task.id, "active")
        interrupted = self.store.begin_execution(task.id, step_id=step.id, capability="external.once")
        recovery = TaskRuntime(store=self.store, capabilities=capabilities).recover_interrupted(task.id)
        self.assertEqual(recovery.failed_closed, 1)
        self.assertEqual(recovery.status, "failed")
        self.assertEqual(self.store.get_execution(interrupted.id).status, "fail")
        self.assertEqual(len(self.store.list_executions(task.id)), 1)

    def test_eval_competence_score_survives_router_restart(self):
        providers = ProviderRegistry()

        class Provider:
            def __init__(self, key):
                self.spec = ProviderSpec(
                    key,
                    key,
                    "fake",
                    {"reasoning.general": 0.5},
                    priority=50,
                )

            def generate(self, request):
                return ModelResponse("ok", self.spec.key, self.spec.model, {}, {})

        providers.register(Provider("a"))
        providers.register(Provider("b"))
        score_store = ProviderScoreStore(self.store.path)
        score_store.initialize()
        first_router = ModelRouter(providers, score_store=score_store)
        report = EvalHarness().run(
            "reasoning.general",
            (EvalCase("case", 1),),
            runner=lambda case: True,
            grader=lambda case, output: (True, "pass"),
            k=1,
        )
        record_eval_report(first_router, "b", report)
        first_router.record_eval_score("a", "reasoning.general", 0.2)

        second_router = ModelRouter(providers, score_store=score_store)
        spec = CapabilitySpec(
            id="reasoning.general",
            description="reason",
            executor_kind="model",
            verifier_id="core.nonempty",
        )
        self.assertEqual(
            second_router.select(spec, context_chars=100).provider.spec.key,
            "b",
        )
        stored = score_store.get("b", "reasoning.general")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.source, "eval:pass_at_1")

    def test_planner_rejects_plan_that_does_not_cover_success_criteria(self):
        class PlannerProvider:
            def __init__(self):
                self.spec = ProviderSpec(
                    "planner",
                    "planner",
                    "fake",
                    {"planning.general": 1.0},
                )

            def generate(self, request):
                return ModelResponse(
                    '{"steps":[{"key":"a","description":"work","capability":"demo.work","dependencies":[],"satisfies_criteria":[]}],"notes":[]}',
                    "planner",
                    "planner",
                    {},
                    {"input_tokens": 10, "output_tokens": 10},
                )

        providers = ProviderRegistry()
        providers.register(PlannerProvider())
        planning = CapabilitySpec(
            id="planning.general",
            description="plan",
            executor_kind="model",
            required_authority="interpret",
            verifier_id="core.nonempty",
        )
        planner = TaskPlanner(
            store=self.store,
            model_router=ModelRouter(providers),
            planning_capability=planning,
            capability_manifest=[{"id": "demo.work"}],
        )
        with self.assertRaises(PlanError):
            planner.plan_and_create(
                objective="Do work",
                success_criteria=("must be covered",),
                authority_scope="interpret",
            )
        task = self.store.list_tasks()[0]
        self.assertEqual(task.status, "failed")
        execution = self.store.list_executions(task.id)[0]
        self.assertEqual(execution.status, "fail")

    def test_cost_budget_blocks_model_call_before_spend(self):
        class CostlyProvider:
            def __init__(self):
                self.spec = ProviderSpec(
                    "costly",
                    "model",
                    "fake",
                    {"reasoning.general": 1.0},
                    input_cost_per_million=100.0,
                    output_cost_per_million=100.0,
                )
                self.calls = 0

            def generate(self, request):
                self.calls += 1
                return ModelResponse(
                    "answer",
                    self.spec.key,
                    self.spec.model,
                    {},
                    {"input_tokens": 10, "output_tokens": 10},
                )

        provider = CostlyProvider()
        providers = ProviderRegistry()
        providers.register(provider)
        capabilities = CapabilityRegistry()
        capabilities.register(
            CapabilitySpec(
                id="reasoning.general",
                description="reason",
                executor_kind="model",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=2, max_output_chars=8000),
            )
        )
        task = self.task(authority="interpret")
        self.store.add_step(
            task.id,
            description="Reason",
            capability="reasoning.general",
            metadata={"accept_all_criteria": True},
        )
        runtime = TaskRuntime(
            store=self.store,
            capabilities=capabilities,
            model_router=ModelRouter(providers),
            budget=RuntimeBudget(max_cost_usd=0.000001),
        )
        result = runtime.run_until_blocked(task.id)
        self.assertEqual(result.status, "waiting")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.store.list_executions(task.id)[0].status, "blocked")

    def test_presenter_uses_durable_criterion_evidence(self):
        capabilities = CapabilityRegistry()
        capabilities.register(
            CapabilitySpec(
                id="demo.present",
                description="produce",
                executor_kind="deterministic",
                verifier_id="core.nonempty",
            ),
            lambda request: CapabilityOutcome("pass", output={"answer": 42}),
        )
        task = self.task()
        self.store.add_step(
            task.id,
            description="Produce",
            capability="demo.present",
            metadata={"accept_all_criteria": True},
        )
        TaskRuntime(store=self.store, capabilities=capabilities).run_until_blocked(task.id)
        presentation = TaskPresenter(self.store).build(task.id)
        self.assertEqual(presentation.status, "completed")
        self.assertTrue(presentation.outputs)
        self.assertIn("answer", presentation.outputs[0]["preview"])
        self.assertIn("accepted", presentation.render_markdown())

    def test_successful_side_effect_receipt_is_durable_evidence(self):
        capabilities = CapabilityRegistry()
        capabilities.register(
            CapabilitySpec(
                id="external.receipted",
                description="receipted action",
                executor_kind="tool",
                required_authority="communicate",
                side_effects=("external_change",),
                idempotent=False,
                verifier_id="core.receipt",
            ),
            lambda request: CapabilityOutcome(
                "pass",
                output=None,
                receipt={"ok": True, "external_id": "r1"},
                claims=(
                    {
                        "kind": "executed",
                        "subject": "external.receipted",
                        "value": "done",
                    },
                ),
            ),
        )
        task = self.task(authority="communicate")
        self.store.add_step(
            task.id,
            description="Do external action",
            capability="external.receipted",
            metadata={"accept_all_criteria": True},
        )
        result = TaskRuntime(store=self.store, capabilities=capabilities).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        receipts = [a for a in self.store.list_artifacts(task.id) if a.kind == "execution_receipt"]
        self.assertEqual(len(receipts), 1)
        criterion = self.store.list_criteria(task.id)[0]
        self.assertIn(receipts[0].id, criterion.evidence_artifact_ids)
        claim = self.store.list_claims(task.id)[0]
        self.assertIn(receipts[0].id, claim.evidence_artifact_ids)

    def test_observer_failure_cannot_break_runtime_event_delivery(self):
        bus = EventBus()
        delivered = []
        bus.subscribe("*", lambda event: (_ for _ in ()).throw(RuntimeError("observer broke")))
        bus.subscribe("*", lambda event: delivered.append(event.name))
        bus.emit(RuntimeEvent("task.started", "task_x"))
        self.assertEqual(delivered, ["task.started"])
        self.assertEqual(len(bus.errors()), 1)

    def test_step_claim_is_atomic_across_concurrent_runtimes(self):
        task = self.task()
        step = self.store.add_step(task.id, description="Claim me", capability="demo.claim")
        self.store.set_task_status(task.id, "active")
        barrier = threading.Barrier(2)
        successes = []
        failures = []

        def claim():
            local = TaskStore(self.store.path)
            barrier.wait()
            try:
                successes.append(local.begin_execution(task.id, step_id=step.id, capability="demo.claim"))
            except InvalidTransitionError as exc:
                failures.append(str(exc))

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(len(self.store.list_executions(task.id)), 1)


if __name__ == "__main__":
    unittest.main()
