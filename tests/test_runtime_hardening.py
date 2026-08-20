from __future__ import annotations
from tests.capability_fixtures import make_registration, register_cap

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from atlas_core.capabilities import (
    CapabilityOutcome,
    CapabilityRegistry,
    
    ExecutionBudget,
)
from atlas_core.evals import EvalCase, EvalHarness, record_eval_report
from atlas_core.events import EventBus, RuntimeEvent
from atlas_core.planner import PlanError, TaskPlanner
from atlas_core.presentation import TaskPresenter
from atlas_core.providers import (
    ModelResponse,
    ModelRouter,
    ProviderHTTPError,
    ProviderRegistry,
    ProviderScoreStore,
    ProviderSpec,
)
from atlas_core.runtime import RuntimeBudget, TaskRuntime
from atlas_core.runtime_execution import _exclude_provider_keys, _provider_failure_is_permanent
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
        self.assertEqual(int(row["version"]), 2)

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
        capabilities = CapabilityRegistry()
        capabilities.register(
            make_registration(
                id="external.send",
                description="send",
                executor_kind="tool",
                required_authority="communicate",
                side_effects=("external_message",),
                idempotent=False,
                verifier_id="core.receipt",
            ),
            lambda request: CapabilityOutcome("pass", output={"sent": True}),
        )
        task = self.task(authority="communicate")
        self.store.add_step(
            task.id,
            description="Send once",
            capability="external.send",
            metadata={"accept_all_criteria": True},
        )
        result = TaskRuntime(store=self.store, capabilities=capabilities, tool_gateway=gateway).run_until_blocked(task.id)
        self.assertEqual(result.status, "failed")
        execution = self.store.list_executions(task.id)[0]
        self.assertEqual(execution.status, "fail")
        self.assertIn("receipt", execution.error or "")

    def test_execution_record_exists_before_handler_runs(self):
        capabilities = CapabilityRegistry()
        observed = {"running": False}
        def handler(request):
            executions = self.store.list_executions(request.task_id, step_id=request.step_id)
            observed["running"] = bool(executions and executions[-1].status == "running")
            return CapabilityOutcome("pass", output={"ok": True})
        capabilities.register(make_registration(id="demo.safe", description="safe", executor_kind="deterministic", verifier_id="core.nonempty"), handler)
        task = self.task()
        self.store.add_step(task.id, description="Safe work", capability="demo.safe", metadata={"accept_all_criteria": True})
        result = TaskRuntime(store=self.store, capabilities=capabilities).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertTrue(observed["running"])

    def test_unknown_capability_fails_durably_without_escaping_runtime(self):
        task = self.task()
        self.store.add_step(task.id, description="Unknown work", capability="missing.capability")
        result = TaskRuntime(store=self.store, capabilities=CapabilityRegistry()).run_until_blocked(task.id)
        self.assertEqual(result.status, "failed")
        executions = self.store.list_executions(task.id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].status, "fail")
        self.assertIn("Unknown capability", executions[0].error)

    def test_human_gate_completes_after_one_explicit_approval(self):
        capabilities = CapabilityRegistry()
        capabilities.register(make_registration(id="human.confirm", description="human confirmation", executor_kind="human", required_authority="recommend", output_kind="human_decision", verifier_id=None, verification_required=False))
        task = self.task(authority="read")
        step = self.store.add_step(task.id, description="Confirm action", capability="human.confirm", metadata={"accept_all_criteria": True})
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
        capabilities.register(make_registration(id="demo.retry", description="retryable", executor_kind="deterministic", verifier_id="core.nonempty", budget=ExecutionBudget(max_attempts=3)), lambda request: CapabilityOutcome("pass", output={"ok": True}))
        task = self.task()
        step = self.store.add_step(task.id, description="Retryable", capability="demo.retry", metadata={"accept_all_criteria": True})
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
        capabilities.register(make_registration(id="external.once", description="external once", executor_kind="tool", required_authority="communicate", side_effects=("external_change",), idempotent=False, verifier_id="core.receipt"), lambda request: CapabilityOutcome("pass", output={"done": True}, receipt={"ok": True}))
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
            def __init__(self, key): self.spec = ProviderSpec(key, key, "fake", {"reasoning.general": 0.5}, priority=50)
            def generate(self, request): return ModelResponse("ok", self.spec.key, self.spec.model, {}, {})
        providers.register(Provider("a")); providers.register(Provider("b"))
        score_store = ProviderScoreStore(self.store.path); score_store.initialize()
        first_router = ModelRouter(providers, score_store=score_store)
        report = EvalHarness().run("reasoning.general", (EvalCase("case", 1),), runner=lambda case: True, grader=lambda case, output: (True, "pass"), k=1)
        record_eval_report(first_router, "b", report); first_router.record_eval_score("a", "reasoning.general", 0.2)
        second_router = ModelRouter(providers, score_store=score_store)
        spec = make_registration(id="reasoning.general", description="reason", executor_kind="model", verifier_id="core.nonempty")
        self.assertEqual(second_router.select(spec.id, context_chars=100).provider.spec.key, "b")
        stored = score_store.get("b", "reasoning.general")
        self.assertIsNotNone(stored); self.assertEqual(stored.source, "eval:pass_at_1")

    def test_planner_rejects_plan_that_does_not_cover_success_criteria(self):
        class PlannerProvider:
            def __init__(self): self.spec = ProviderSpec("planner", "planner", "fake", {"planning.general": 1.0})
            def generate(self, request): return ModelResponse('{"steps":[{"key":"a","description":"work","capability":"demo.work","dependencies":[],"satisfies_criteria":[]}],"notes":[]}', "planner", "planner", {}, {"input_tokens": 10, "output_tokens": 10})
        providers = ProviderRegistry(); providers.register(PlannerProvider())
        planning = make_registration(id="planning.general", description="plan", executor_kind="model", required_authority="interpret", verifier_id="core.nonempty")
        planner = TaskPlanner(store=self.store, model_router=ModelRouter(providers), planning_capability=planning, capability_manifest=[{"id": "demo.work"}])
        with self.assertRaises(PlanError): planner.plan_and_create(objective="Do work", success_criteria=("must be covered",), authority_scope="interpret")
        task = self.store.list_tasks()[0]; self.assertEqual(task.status, "failed")
        execution = self.store.list_executions(task.id)[0]; self.assertEqual(execution.status, "fail")

    def _planner(self, text, *, scores=None, manifest=None):
        class Provider:
            def __init__(self):
                self.spec = ProviderSpec("planner", "planner", "fake", scores or {"planning.general": 1.0})
                self.text = text
            def generate(self, request):
                return ModelResponse(self.text, self.spec.key, self.spec.model, {}, {"input_tokens": 10, "output_tokens": 10})
        providers = ProviderRegistry()
        providers.register(Provider())
        planning = make_registration(
            id="planning.general",
            description="plan",
            executor_kind="model",
            required_authority="interpret",
            verifier_id="core.nonempty",
        )
        return TaskPlanner(
            store=self.store,
            model_router=ModelRouter(providers),
            planning_capability=planning,
            capability_manifest=manifest or [{"id": "demo.work"}],
        )

    def test_planner_rejects_knowledge_answer_without_search_results(self):
        planner = self._planner(
            '{"steps":[{"key":"a","description":"Answer","capability":"knowledge.answer","dependencies":[],"satisfies_criteria":[1]}],"notes":[]}',
            manifest=[{
                "id": "knowledge.answer",
                "executor_kind": "deterministic",
                "output_kind": "grounded_answer",
                "requires_artifact_kinds": ["knowledge_search_results"],
            }],
        )
        with self.assertRaises(PlanError) as raised:
            planner.plan_and_create(objective="what is ohms law", success_criteria=("truthful answer",), authority_scope="interpret")
        self.assertIn("knowledge_search_results", str(raised.exception))
        self.assertEqual(self.store.list_tasks()[-1].status, "failed")

    def test_planner_accepts_search_then_answer(self):
        planner = self._planner(
            '{"steps":[{"key":"s","description":"Search","capability":"knowledge.search","dependencies":[],"satisfies_criteria":[]},{"key":"a","description":"Answer","capability":"knowledge.answer","dependencies":["s"],"satisfies_criteria":[1]}],"notes":[]}',
            manifest=[
                {"id": "knowledge.search", "executor_kind": "deterministic", "output_kind": "knowledge_search_results"},
                {"id": "knowledge.answer", "executor_kind": "deterministic", "output_kind": "grounded_answer", "requires_artifact_kinds": ["knowledge_search_results"]},
            ],
        )
        task, plan = planner.plan_and_create(objective="Search Atlas knowledge for: ContextBuilder", success_criteria=("grounded",), authority_scope="interpret")
        self.assertEqual(task.status, "active")
        self.assertEqual([step.capability for step in plan.steps], ["knowledge.search", "knowledge.answer"])

    def test_planner_rejects_unroutable_model_capability(self):
        planner = self._planner(
            '{"steps":[{"key":"a","description":"Analyse","capability":"reasoning.deep_analysis","dependencies":[],"satisfies_criteria":[1]}],"notes":[]}',
            scores={"planning.general": 1.0},
            manifest=[{
                "id": "reasoning.deep_analysis",
                "executor_kind": "model",
                "output_kind": "capability_result",
                "privacy": "cloud_allowed",
                "verifier_id": "core.nonempty",
            }],
        )
        with self.assertRaises(PlanError) as raised:
            planner.plan_and_create(objective="Identify a character", success_criteria=("truthful answer",), authority_scope="interpret")
        self.assertIn("not executable", str(raised.exception))

    def test_planner_accepts_compose_when_a_provider_can_run_it(self):
        planner = self._planner(
            '{"steps":[{"key":"a","description":"Write the story","capability":"generation.compose","dependencies":[],"satisfies_criteria":[1]}],"notes":[]}',
            scores={"planning.general": 1.0, "generation.compose": 0.9},
            manifest=[{
                "id": "generation.compose",
                "executor_kind": "model",
                "output_kind": "capability_result",
                "verifier_id": "core.nonempty",
            }],
        )
        task, plan = planner.plan_and_create(
            objective="tel me a short story about a guy who found a magic pond with water of immortality",
            success_criteria=("make it believable",),
            authority_scope="interpret",
        )
        self.assertEqual(plan.steps[0].capability, "generation.compose")
        self.assertEqual(task.status, "active")

    def _sequential_planner(self, texts, *, scores=None, manifest=None):
        class Provider:
            def __init__(self):
                self.spec = ProviderSpec("planner", "planner", "fake", scores or {"planning.general": 1.0})
                self.texts = list(texts)
                self.calls = 0
                self.requests = []
            def generate(self, request):
                self.calls += 1
                self.requests.append(request)
                text = self.texts[min(self.calls - 1, len(self.texts) - 1)]
                return ModelResponse(text, self.spec.key, self.spec.model, {}, {"input_tokens": 10, "output_tokens": 10})
        provider = Provider()
        providers = ProviderRegistry()
        providers.register(provider)
        planning = make_registration(
            id="planning.general",
            description="plan",
            executor_kind="model",
            required_authority="interpret",
            context_profile="plan",
            verifier_id="core.nonempty",
        )
        planner = TaskPlanner(
            store=self.store,
            model_router=ModelRouter(providers),
            planning_capability=planning,
            capability_manifest=manifest or [{"id": "demo.work"}],
        )
        return planner, provider

    def test_parse_plan_extracts_json_from_markdown_and_prose(self):
        body = '{"steps":[{"key":"a","description":"Work","capability":"demo.work","dependencies":[],"satisfies_criteria":[1]}],"notes":[]}'
        fenced = TaskPlanner.parse_plan(f"```json\n{body}\n```")
        self.assertEqual(fenced.steps[0].key, "a")
        prose = TaskPlanner.parse_plan(f"Here is the plan:\n{body}\nHope this helps.")
        self.assertEqual(prose.steps[0].capability, "demo.work")
        with self.assertRaises(PlanError) as raised:
            TaskPlanner.parse_plan("Sure, I will plan that for you.")
        self.assertEqual(str(raised.exception), "Planner did not return valid JSON.")

    def test_planner_accepts_fenced_json_without_a_repair_call(self):
        body = '{"steps":[{"key":"a","description":"Work","capability":"demo.work","dependencies":[],"satisfies_criteria":[1]}],"notes":[]}'
        planner, provider = self._sequential_planner((f"```json\n{body}\n```",))
        task, plan = planner.plan_and_create(
            objective="Do work",
            success_criteria=("Done",),
            authority_scope="interpret",
        )
        self.assertEqual(task.status, "active")
        self.assertEqual(plan.steps[0].key, "a")
        self.assertEqual(provider.calls, 1)
        planning_execs = [
            item for item in self.store.list_executions(task.id)
            if item.capability == "planning.general"
        ]
        self.assertEqual(len(planning_execs), 1)
        self.assertEqual(planning_execs[0].status, "pass")

    def test_planner_repairs_invalid_json_once(self):
        valid = '{"steps":[{"key":"a","description":"Work","capability":"demo.work","dependencies":[],"satisfies_criteria":[1]}],"notes":[]}'
        planner, provider = self._sequential_planner(("Sure, here is a plan in prose.", valid))
        task, plan = planner.plan_and_create(
            objective="Do work",
            success_criteria=("Done",),
            authority_scope="interpret",
        )
        self.assertEqual(task.status, "active")
        self.assertEqual(plan.steps[0].key, "a")
        self.assertEqual(provider.calls, 2)
        self.assertTrue(provider.requests[1].metadata.get("plan_repair"))
        self.assertIn("Planner did not return valid JSON.", provider.requests[1].input)
        executions = [
            item for item in self.store.list_executions(task.id)
            if item.capability == "planning.general"
        ]
        self.assertEqual([item.status for item in executions], ["rework", "pass"])
        self.assertEqual(len(self.store.list_context_manifests(task.id, step_id=executions[0].step_id)), 2)
        plans = [item for item in self.store.list_artifacts(task.id) if item.kind == "task_plan"]
        self.assertEqual(plans[0].metadata.get("repaired"), True)

    def test_planner_does_not_repair_a_second_time(self):
        planner, provider = self._sequential_planner(("not json", "still not json", '{"steps":[],"notes":[]}'))
        with self.assertRaises(PlanError) as raised:
            planner.plan_and_create(
                objective="Do work",
                success_criteria=("Done",),
                authority_scope="interpret",
            )
        self.assertEqual(str(raised.exception), "Planner did not return valid JSON.")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(self.store.list_tasks()[-1].status, "failed")

    def test_planner_does_not_repair_invalid_plan_content(self):
        planner, provider = self._sequential_planner(
            (
                '{"steps":[{"key":"a","description":"work","capability":"demo.work","dependencies":[],"satisfies_criteria":[]}],"notes":[]}',
                '{"steps":[{"key":"a","description":"work","capability":"demo.work","dependencies":[],"satisfies_criteria":[1]}],"notes":[]}',
            )
        )
        with self.assertRaises(PlanError) as raised:
            planner.plan_and_create(
                objective="Do work",
                success_criteria=("must be covered",),
                authority_scope="interpret",
            )
        self.assertIn("success criteria", str(raised.exception))
        self.assertEqual(provider.calls, 1)

    def test_cost_budget_blocks_model_call_before_spend(self):
        class CostlyProvider:
            def __init__(self):
                self.spec = ProviderSpec("costly", "model", "fake", {"reasoning.general": 1.0}, input_cost_per_million=100.0, output_cost_per_million=100.0); self.calls = 0
            def generate(self, request): self.calls += 1; return ModelResponse("answer", self.spec.key, self.spec.model, {}, {"input_tokens": 10, "output_tokens": 10})
        provider = CostlyProvider(); providers = ProviderRegistry(); providers.register(provider)
        capabilities = CapabilityRegistry(); capabilities.register(make_registration(id="reasoning.general", description="reason", executor_kind="model", verifier_id="core.nonempty", budget=ExecutionBudget(max_attempts=2, max_output_chars=8000)))
        task = self.task(authority="interpret"); self.store.add_step(task.id, description="Reason", capability="reasoning.general", metadata={"accept_all_criteria": True})
        runtime = TaskRuntime(store=self.store, capabilities=capabilities, model_router=ModelRouter(providers), budget=RuntimeBudget(max_cost_usd=0.000001))
        result = runtime.run_until_blocked(task.id)
        self.assertEqual(result.status, "waiting"); self.assertEqual(provider.calls, 0); self.assertEqual(self.store.list_executions(task.id)[0].status, "blocked")

    def test_presenter_uses_durable_criterion_evidence(self):
        capabilities = CapabilityRegistry(); capabilities.register(make_registration(id="demo.present", description="produce", executor_kind="deterministic", verifier_id="core.nonempty"), lambda request: CapabilityOutcome("pass", output={"answer": 42}))
        task = self.task(); self.store.add_step(task.id, description="Produce", capability="demo.present", metadata={"accept_all_criteria": True})
        TaskRuntime(store=self.store, capabilities=capabilities).run_until_blocked(task.id)
        presentation = TaskPresenter(self.store).build(task.id)
        self.assertEqual(presentation.status, "completed"); self.assertTrue(presentation.outputs); self.assertIn("answer", presentation.outputs[0]["preview"]); self.assertIn("accepted", presentation.render_markdown())

    def test_repetitive_model_text_is_not_usable_output(self):
        registry = VerifierRegistry()
        looping = "\n".join(['*   *Could it be **The Adventures of Tintin**?*'] * 40)
        result = registry.verify(
            "core.nonempty",
            make_registration(id="reasoning.general", description="reason", executor_kind="model", verifier_id="core.nonempty").profile,
            looping,
            {},
        )
        self.assertEqual(result.status, "rework")
        self.assertIn("looping", result.summary)
        ok = registry.verify(
            "core.nonempty",
            make_registration(id="reasoning.general", description="reason", executor_kind="model", verifier_id="core.nonempty").profile,
            "Plastic Man",
            {},
        )
        self.assertEqual(ok.status, "pass")

    def test_looping_reasoner_cannot_complete_a_truthful_answer(self):
        class LoopingProvider:
            def __init__(self):
                self.spec = ProviderSpec("loop", "loop", "fake", {"reasoning.general": 1.0})
                self.calls = 0
            def generate(self, request):
                self.calls += 1
                text = "\n".join(["*   *Could it be **The Adventures of Tintin**?*"] * 40)
                return ModelResponse(text, self.spec.key, self.spec.model, {}, {"output_tokens": 10})
        provider = LoopingProvider()
        providers = ProviderRegistry(); providers.register(provider)
        capabilities = CapabilityRegistry()
        capabilities.register(
            make_registration(
                id="reasoning.general",
                description="reason",
                executor_kind="model",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=3),
            )
        )
        task = self.store.create_task(
            objective="Identify the fictional character",
            success_criteria=("produce a truthful answer",),
            authority_scope="interpret",
        )
        self.store.add_step(
            task.id,
            description="Identify the character",
            capability="reasoning.general",
            metadata={"accept_all_criteria": True},
        )
        result = TaskRuntime(
            store=self.store,
            capabilities=capabilities,
            model_router=ModelRouter(providers),
        ).run_until_blocked(task.id)
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.store.list_criteria(task.id)[0].status, "pending")
        executions = self.store.list_executions(task.id)
        self.assertEqual(provider.calls, 3)
        self.assertTrue(all(item.status == "rework" for item in executions))
        self.assertTrue(all(item.provider == "loop" for item in executions))
        self.assertTrue(any("looping" in (item.error or "") for item in executions))
        presentation = TaskPresenter(self.store).build(task.id)
        self.assertIn("looping", presentation.failure_reason or "")
        self.assertNotIn("## Grounded answer", presentation.render_markdown())

    def test_timeout_on_sole_provider_retries_the_same_provider(self):
        class TimeoutThenOk:
            def __init__(self):
                self.spec = ProviderSpec("xai:expert", "grok", "fake", {"reasoning.general": 1.0})
                self.calls = 0
            def generate(self, request):
                self.calls += 1
                if self.calls == 1:
                    raise ProviderHTTPError("Provider connection failed: timed out")
                return ModelResponse("Plastic Man", self.spec.key, self.spec.model, {}, {"output_tokens": 8})
        provider = TimeoutThenOk()
        providers = ProviderRegistry(); providers.register(provider)
        capabilities = CapabilityRegistry()
        capabilities.register(
            make_registration(
                id="reasoning.general",
                description="reason",
                executor_kind="model",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=3),
            )
        )
        task = self.store.create_task(
            objective="Identify the fictional character",
            success_criteria=("produce a truthful answer",),
            authority_scope="interpret",
        )
        self.store.add_step(
            task.id,
            description="Identify the character",
            capability="reasoning.general",
            metadata={"accept_all_criteria": True},
        )
        result = TaskRuntime(
            store=self.store,
            capabilities=capabilities,
            model_router=ModelRouter(providers),
        ).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(provider.calls, 2)
        executions = self.store.list_executions(task.id)
        self.assertEqual([item.provider for item in executions], ["xai:expert", "xai:expert"])
        self.assertEqual(executions[0].status, "abstain")
        self.assertIn("timed out", executions[0].error or "")
        self.assertEqual(executions[1].status, "pass")
        self.assertEqual(self.store.list_criteria(task.id)[0].status, "accepted")

    def test_timeout_fails_over_when_another_provider_remains(self):
        class TimeoutProvider:
            def __init__(self):
                self.spec = ProviderSpec("first", "m1", "fake", {"reasoning.general": 0.99}, priority=100)
                self.calls = 0
            def generate(self, request):
                self.calls += 1
                raise ProviderHTTPError("Provider connection failed: timed out")
        class OkProvider:
            def __init__(self):
                self.spec = ProviderSpec("second", "m2", "fake", {"reasoning.general": 0.90}, priority=90)
                self.calls = 0
            def generate(self, request):
                self.calls += 1
                return ModelResponse("recovered", self.spec.key, self.spec.model, {}, {"output_tokens": 8})
        first = TimeoutProvider(); second = OkProvider()
        providers = ProviderRegistry(); providers.register(first); providers.register(second)
        capabilities = CapabilityRegistry()
        capabilities.register(
            make_registration(
                id="reasoning.general",
                description="reason",
                executor_kind="model",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=3),
            )
        )
        task = self.task(authority="interpret")
        self.store.add_step(
            task.id,
            description="Reason",
            capability="reasoning.general",
            metadata={"accept_all_criteria": True},
        )
        result = TaskRuntime(
            store=self.store,
            capabilities=capabilities,
            model_router=ModelRouter(providers),
        ).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)
        executions = self.store.list_executions(task.id)
        self.assertEqual([item.provider for item in executions], ["first", "second"])
        self.assertEqual(executions[0].status, "abstain")
        self.assertEqual(executions[1].status, "pass")

    def test_auth_http_error_excludes_the_sole_provider(self):
        class AuthFail:
            def __init__(self):
                self.spec = ProviderSpec("xai:expert", "grok", "fake", {"reasoning.general": 1.0})
                self.calls = 0
            def generate(self, request):
                self.calls += 1
                raise ProviderHTTPError("HTTP 401 from provider: unauthorized")
        provider = AuthFail()
        providers = ProviderRegistry(); providers.register(provider)
        capabilities = CapabilityRegistry()
        capabilities.register(
            make_registration(
                id="reasoning.general",
                description="reason",
                executor_kind="model",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=3),
            )
        )
        task = self.task(authority="interpret")
        self.store.add_step(
            task.id,
            description="Reason",
            capability="reasoning.general",
            metadata={"accept_all_criteria": True},
        )
        result = TaskRuntime(
            store=self.store,
            capabilities=capabilities,
            model_router=ModelRouter(providers),
        ).run_until_blocked(task.id)
        self.assertNotEqual(result.status, "completed")
        self.assertEqual(provider.calls, 1)
        executions = self.store.list_executions(task.id)
        self.assertEqual(executions[0].status, "abstain")
        self.assertIn("HTTP 401", executions[0].error or "")
        self.assertTrue(any(item.status == "blocked" for item in executions[1:]))

    def test_auth_http_error_fails_over_to_next_provider(self):
        class AuthFail:
            def __init__(self):
                self.spec = ProviderSpec("first", "m1", "fake", {"reasoning.general": 0.99}, priority=100)
                self.calls = 0
            def generate(self, request):
                self.calls += 1
                raise ProviderHTTPError("HTTP 403 from provider: forbidden")
        class OkProvider:
            def __init__(self):
                self.spec = ProviderSpec("second", "m2", "fake", {"reasoning.general": 0.90}, priority=90)
                self.calls = 0
            def generate(self, request):
                self.calls += 1
                return ModelResponse("recovered", self.spec.key, self.spec.model, {}, {"output_tokens": 8})
        first = AuthFail(); second = OkProvider()
        providers = ProviderRegistry(); providers.register(first); providers.register(second)
        capabilities = CapabilityRegistry()
        capabilities.register(
            make_registration(
                id="reasoning.general",
                description="reason",
                executor_kind="model",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=3),
            )
        )
        task = self.task(authority="interpret")
        self.store.add_step(
            task.id,
            description="Reason",
            capability="reasoning.general",
            metadata={"accept_all_criteria": True},
        )
        result = TaskRuntime(
            store=self.store,
            capabilities=capabilities,
            model_router=ModelRouter(providers),
        ).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)
        executions = self.store.list_executions(task.id)
        self.assertEqual([item.provider for item in executions], ["first", "second"])
        self.assertEqual(executions[0].status, "abstain")
        self.assertEqual(executions[1].status, "pass")

    def test_exclude_provider_keys_keeps_sole_transient_and_exiles_auth(self):
        timeout = SimpleNamespace(
            provider="xai:expert",
            status="abstain",
            error="Provider connection failed: timed out",
        )
        auth = SimpleNamespace(
            provider="xai:expert",
            status="abstain",
            error="HTTP 401 from provider: unauthorized",
        )
        rework = SimpleNamespace(
            provider="loop",
            status="rework",
            error="output appears to be looping",
        )
        self.assertFalse(_provider_failure_is_permanent("abstain", timeout.error))
        self.assertTrue(_provider_failure_is_permanent("abstain", auth.error))
        self.assertFalse(_provider_failure_is_permanent("rework", rework.error))
        self.assertEqual(
            _exclude_provider_keys((timeout,), remaining_route_exists=lambda exclude: False),
            (),
        )
        self.assertEqual(
            _exclude_provider_keys((timeout,), remaining_route_exists=lambda exclude: True),
            ("xai:expert",),
        )
        self.assertEqual(
            _exclude_provider_keys((auth,), remaining_route_exists=lambda exclude: False),
            ("xai:expert",),
        )
        self.assertEqual(
            _exclude_provider_keys((rework,), remaining_route_exists=lambda exclude: True),
            (),
        )

    def test_successful_side_effect_receipt_is_durable_evidence(self):
        capabilities = CapabilityRegistry(); capabilities.register(make_registration(id="external.receipted", description="receipted action", executor_kind="tool", required_authority="communicate", side_effects=("external_change",), idempotent=False, verifier_id="core.receipt"), lambda request: CapabilityOutcome("pass", output=None, receipt={"ok": True, "external_id": "r1"}, claims=({"kind": "executed", "subject": "external.receipted", "value": "done"},)))
        task = self.task(authority="communicate"); self.store.add_step(task.id, description="Do external action", capability="external.receipted", metadata={"accept_all_criteria": True})
        result = TaskRuntime(store=self.store, capabilities=capabilities).run_until_blocked(task.id); self.assertEqual(result.status, "completed")
        receipts = [a for a in self.store.list_artifacts(task.id) if a.kind == "execution_receipt"]; self.assertEqual(len(receipts), 1)
        criterion = self.store.list_criteria(task.id)[0]; self.assertIn(receipts[0].id, criterion.evidence_artifact_ids)
        claim = self.store.list_claims(task.id)[0]; self.assertIn(receipts[0].id, claim.evidence_artifact_ids)

    def test_observer_failure_cannot_break_runtime_event_delivery(self):
        bus = EventBus(); delivered = []
        bus.subscribe("*", lambda event: (_ for _ in ()).throw(RuntimeError("observer broke"))); bus.subscribe("*", lambda event: delivered.append(event.name)); bus.emit(RuntimeEvent("task.started", "task_x"))
        self.assertEqual(delivered, ["task.started"]); self.assertEqual(len(bus.errors()), 1)

    def test_step_claim_is_atomic_across_concurrent_runtimes(self):
        task = self.task(); step = self.store.add_step(task.id, description="Claim me", capability="demo.claim"); self.store.set_task_status(task.id, "active")
        barrier = threading.Barrier(2); successes = []; failures = []
        def claim():
            local = TaskStore(self.store.path); barrier.wait()
            try: successes.append(local.begin_execution(task.id, step_id=step.id, capability="demo.claim"))
            except InvalidTransitionError as exc: failures.append(str(exc))
        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(successes), 1); self.assertEqual(len(failures), 1); self.assertEqual(len(self.store.list_executions(task.id)), 1)


if __name__ == "__main__":
    unittest.main()
