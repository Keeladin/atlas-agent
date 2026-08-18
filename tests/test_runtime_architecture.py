from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from atlas_core.authority import authority_allows
from atlas_core.capabilities import CapabilityOutcome, CapabilityRegistry, CapabilitySpec, ExecutionBudget
from atlas_core.context import ContextBuilder
from atlas_core.evals import EvalCase, EvalHarness
from atlas_core.providers import (
    AnthropicMessagesProvider, GeminiGenerateContentProvider, ModelRequest, ModelResponse, ModelRouter, OpenAIResponsesProvider, ProviderRegistry, ProviderSpec
)
from atlas_core.runtime import RuntimeBudget, TaskRuntime
from atlas_core.planner import TaskPlanner
from atlas_core.tools import MCPToolBridge, ToolGateway, ToolResult, ToolSpec
from atlas_core.tasks import InvalidTransitionError, TaskStore
from atlas_core.verification import VerificationResult, VerifierRegistry


class FakeProvider:
    def __init__(self, spec, text="model result"):
        self.spec = spec
        self.text = text
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return ModelResponse(self.text, self.spec.key, self.spec.model, {"ok": True}, {"output_tokens": 10})


class AtlasRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "atlas.db"
        self.store = TaskStore(self.db_path)
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def _task(self, authority="read", criteria=("Done",)):
        return self.store.create_task(objective="Complete a bounded job", success_criteria=criteria, authority_scope=authority)

    def test_task_state_survives_reopen(self):
        task = self._task()
        step = self.store.add_step(task.id, description="Do work", capability="demo.work")
        reopened = TaskStore(self.db_path)
        reopened.initialize()
        self.assertEqual(reopened.get_task(task.id).objective, task.objective)
        self.assertEqual(reopened.get_step(step.id).capability, "demo.work")

    def test_artifacts_are_immutable_and_hashed(self):
        task = self._task()
        artifact = self.store.put_artifact(task.id, kind="raw_source", payload={"a": 1})
        self.assertEqual(len(artifact.sha256), 64)
        self.assertEqual(self.store.get_artifact(artifact.id).payload, {"a": 1})
        with self.store._db() as db:
            row = db.execute("SELECT COUNT(*) AS n FROM task_artifacts WHERE id=?", (artifact.id,)).fetchone()
        self.assertEqual(row["n"], 1)

    def test_ready_steps_follow_dependency_truth(self):
        task = self._task()
        first = self.store.add_step(task.id, description="First", capability="demo.first")
        second = self.store.add_step(task.id, description="Second", capability="demo.second", dependencies=[first.id])
        self.assertEqual([x.id for x in self.store.ready_steps(task.id)], [first.id])
        self.store.set_step_status(first.id, "running")
        self.store.set_step_status(first.id, "pass")
        self.assertEqual([x.id for x in self.store.ready_steps(task.id)], [second.id])

    def test_execution_is_terminal_once(self):
        task = self._task()
        step = self.store.add_step(task.id, description="Work", capability="demo")
        execution = self.store.begin_execution(task.id, step_id=step.id, capability="demo")
        self.store.finish_execution(execution.id, status="pass")
        with self.assertRaises(InvalidTransitionError):
            self.store.finish_execution(execution.id, status="pass")

    def test_claims_keep_epistemic_class_and_evidence(self):
        task = self._task()
        artifact = self.store.put_artifact(task.id, kind="source", payload="sensor says 4")
        claim = self.store.add_claim(task.id, kind="observed", subject="sensor.value", value=4, evidence_artifact_ids=[artifact.id])
        self.assertEqual(claim.kind, "observed")
        self.assertEqual(claim.evidence_artifact_ids, (artifact.id,))
        with self.assertRaises(ValueError):
            self.store.add_claim(task.id, kind="retrieved", subject="x", value=1)

    def test_checkpoint_references_artifacts_without_payload_duplication(self):
        task = self._task()
        self.store.put_artifact(task.id, kind="large", payload={"text": "x" * 1000})
        checkpoint = self.store.create_checkpoint(task.id, reason="milestone")
        self.assertIn("sha256", checkpoint.snapshot["artifacts"][0])
        self.assertNotIn("payload", checkpoint.snapshot["artifacts"][0])

    def test_authority_ladder_is_monotonic(self):
        self.assertTrue(authority_allows("execute_external", "read"))
        self.assertTrue(authority_allows("recommend", "interpret"))
        self.assertFalse(authority_allows("read", "communicate"))

    def test_context_builder_omits_large_artifact_payload_but_preserves_reference(self):
        task = self._task()
        step = self.store.add_step(task.id, description="Analyse", capability="reasoning.general")
        artifact = self.store.put_artifact(task.id, step_id=step.id, kind="manual", payload="x" * 5000, metadata={"page": 1})
        pack = ContextBuilder(self.store).build(task.id, step.id, artifact_ids=(artifact.id,), profile="research", max_chars=2500)
        self.assertIn(artifact.id, pack.omitted_artifact_ids)
        self.assertEqual(pack.payload["omitted_artifacts"][0]["sha256"], artifact.sha256)

    def test_deterministic_runtime_completes_multi_step_task(self):
        caps = CapabilityRegistry()
        verifiers = VerifierRegistry()

        def handler(request):
            return CapabilityOutcome("pass", output={"step": request.step_id})

        for name in ("demo.a", "demo.b"):
            caps.register(CapabilitySpec(
                id=name,
                description=name,
                executor_kind="deterministic",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=2),
                parallel_safe=True,
            ), handler)
        task = self._task(criteria=("A done", "B done"))
        a = self.store.add_step(task.id, description="A", capability="demo.a", metadata={"satisfies_criteria": [1]})
        self.store.add_step(task.id, description="B", capability="demo.b", dependencies=[a.id], metadata={"satisfies_criteria": [2]})
        result = TaskRuntime(store=self.store, capabilities=caps, verifiers=verifiers).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertTrue(all(x.status == "accepted" for x in self.store.list_criteria(task.id)))
        self.assertEqual(len(self.store.list_executions(task.id)), 2)

    def test_rework_retries_without_turn_depth_limit(self):
        caps = CapabilityRegistry()
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return CapabilityOutcome("pass", output="good" if calls["n"] >= 2 else "bad")

        verifiers = VerifierRegistry()
        def verifier(spec, output, context):
            return VerificationResult("pass" if output == "good" else "rework", "checked")
        verifiers.register("demo.retry", verifier)
        caps.register(CapabilitySpec(id="demo.retry", description="retry", executor_kind="deterministic", verifier_id="demo.retry", budget=ExecutionBudget(max_attempts=3)), handler)
        task = self._task()
        self.store.add_step(task.id, description="Retry work", capability="demo.retry", metadata={"accept_all_criteria": True})
        result = TaskRuntime(store=self.store, capabilities=caps, verifiers=verifiers).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.store.list_executions(task.id)), 2)

    def test_authority_can_pause_then_resume_after_explicit_approval(self):
        caps = CapabilityRegistry()
        caps.register(CapabilitySpec(id="external.send", description="send", executor_kind="tool", required_authority="communicate", verifier_id="core.receipt"), lambda request: CapabilityOutcome("pass", output={"sent": True}, receipt={"ok": True, "message_id": "m1"}))
        task = self._task(authority="read")
        step = self.store.add_step(task.id, description="Send", capability="external.send", metadata={"accept_all_criteria": True})
        runtime = TaskRuntime(store=self.store, capabilities=caps)
        first = runtime.run_until_blocked(task.id)
        self.assertEqual(first.status, "waiting")
        approval = self.store.list_approvals(task.id, status="pending")[0]
        self.store.decide_approval(approval.id, status="approved", note="explicitly approved")
        second = runtime.run_until_blocked(task.id)
        self.assertEqual(second.status, "completed")
        self.assertEqual(self.store.get_step(step.id).status, "pass")

    def test_model_router_honours_local_only_and_capability_score(self):
        registry = ProviderRegistry()
        local = FakeProvider(ProviderSpec("local", "qwen", "fake", {"reasoning.deep": 0.8}, local=True, priority=50))
        cloud = FakeProvider(ProviderSpec("cloud", "frontier", "fake", {"reasoning.deep": 0.99}, local=False, priority=100))
        registry.register(local)
        registry.register(cloud)
        spec = CapabilitySpec(id="reasoning.deep", description="deep", executor_kind="model", verifier_id="core.nonempty", privacy="local_only")
        route = ModelRouter(registry).select(spec, context_chars=1000)
        self.assertEqual(route.provider.spec.key, "local")

    def test_model_capability_routes_and_records_provider(self):
        providers = ProviderRegistry()
        fake = FakeProvider(ProviderSpec("cloud:test", "model-x", "fake", {"reasoning.general": 0.9}, priority=100))
        providers.register(fake)
        caps = CapabilityRegistry()
        caps.register(CapabilitySpec(id="reasoning.general", description="reason", executor_kind="model", verifier_id="core.nonempty"))
        task = self._task()
        self.store.add_step(task.id, description="Reason", capability="reasoning.general", metadata={"accept_all_criteria": True})
        result = TaskRuntime(store=self.store, capabilities=caps, model_router=ModelRouter(providers)).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        execution = self.store.list_executions(task.id)[0]
        self.assertEqual(execution.provider, "cloud:test")
        self.assertEqual(fake.calls, 1)

    def test_runtime_budget_is_task_lifetime_budget_not_tool_rounds(self):
        caps = CapabilityRegistry()
        caps.register(CapabilitySpec(id="demo", description="demo", executor_kind="deterministic", verifier_id="core.nonempty"), lambda request: CapabilityOutcome("pass", output=request.step_id))
        task = self._task(criteria=tuple(f"c{i}" for i in range(1, 8)))
        previous = None
        for i in range(1, 8):
            step = self.store.add_step(task.id, description=f"s{i}", capability="demo", dependencies=([previous] if previous else []), metadata={"satisfies_criteria": [i]})
            previous = step.id
        result = TaskRuntime(store=self.store, capabilities=caps, budget=RuntimeBudget(max_executions=20, max_cycles=20)).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.store.list_executions(task.id)), 7)

    def test_events_are_durable(self):
        task = self._task()
        self.store.append_event(task.id, name="task.created", payload={"x": 1})
        reopened = TaskStore(self.db_path)
        events = reopened.list_events(task.id)
        self.assertEqual(events[0].name, "task.created")
        self.assertEqual(events[0].payload, {"x": 1})

    def test_eval_harness_reports_pass_at_k_and_pass_all_k(self):
        cases = (EvalCase("a", 1), EvalCase("b", 2))
        attempts = {1: 0, 2: 0}
        def runner(case):
            attempts[case.input] += 1
            return case.input == 1 or attempts[case.input] >= 2
        def grader(case, output):
            return bool(output), "ok" if output else "no"
        report = EvalHarness().run("demo", cases, runner=runner, grader=grader, k=3)
        self.assertEqual(report.pass_at_1, 0.5)
        self.assertEqual(report.pass_at_k, 1.0)
        self.assertEqual(report.pass_all_k, 0.5)

    def test_tool_gateway_requires_receipt_for_side_effects(self):
        gateway = ToolGateway()
        gateway.register(
            ToolSpec("mail.send", "send mail", required_authority="communicate", side_effects=("external_email",), idempotent=False),
            lambda arguments: ToolResult(True, output={"sent": True}),
        )
        result = gateway.invoke("mail.send", {"to": "x@example.com"}, authority_scope="communicate")
        self.assertFalse(result.ok)
        self.assertIn("receipt", result.error)

    def test_non_idempotent_side_effect_is_not_blindly_retried(self):
        caps = CapabilityRegistry()
        calls = {"n": 0}
        def handler(request):
            calls["n"] += 1
            return CapabilityOutcome("pass", output={"sent": True}, receipt={"ok": True, "message_id": "m1"})
        verifiers = VerifierRegistry()
        verifiers.register("mail.verify", lambda spec, output, context: VerificationResult("rework", "delivery state ambiguous"))
        caps.register(CapabilitySpec(
            id="mail.send", description="send once", executor_kind="tool",
            required_authority="communicate", side_effects=("external_email",),
            idempotent=False, verifier_id="mail.verify", budget=ExecutionBudget(max_attempts=3),
        ), handler)
        task = self._task(authority="communicate")
        self.store.add_step(task.id, description="Send", capability="mail.send", metadata={"accept_all_criteria": True})
        result = TaskRuntime(store=self.store, capabilities=caps, verifiers=verifiers).run_until_blocked(task.id)
        self.assertEqual(result.status, "failed")
        self.assertEqual(calls["n"], 1)
        self.assertEqual(len(self.store.list_executions(task.id)), 1)
        self.assertTrue(any(event.name == "retry.blocked" for event in self.store.list_events(task.id)))

    def test_mcp_bridge_is_adapter_not_runtime_core(self):
        class FakeMCP:
            def list_tools(self):
                return [{"name": "lookup", "description": "Lookup", "inputSchema": {"type": "object"}}]
            def call_tool(self, name, arguments):
                return {"content": [{"type": "text", "text": str(arguments.get("q"))}], "isError": False}
        gateway = ToolGateway()
        ids = MCPToolBridge(FakeMCP()).register_discovered(gateway, prefix="mcp.test")
        self.assertEqual(ids, ("mcp.test.lookup",))
        result = gateway.invoke("mcp.test.lookup", {"q": "abc"}, authority_scope="read")
        self.assertTrue(result.ok)

    def test_planner_creates_dependency_graph_from_strict_json(self):
        plan_text = '{"steps":[{"key":"a","description":"Collect","capability":"demo.collect","dependencies":[],"satisfies_criteria":[]},{"key":"b","description":"Finish","capability":"demo.finish","dependencies":["a"],"satisfies_criteria":[1]}],"notes":[]}'
        providers = ProviderRegistry()
        fake = FakeProvider(ProviderSpec("planner", "planner-model", "fake", {"planning.general": 0.9}), text=plan_text)
        providers.register(fake)
        planning_spec = CapabilitySpec(id="planning.general", description="plan", executor_kind="model", verifier_id="core.nonempty")
        planner = TaskPlanner(
            store=self.store,
            model_router=ModelRouter(providers),
            planning_capability=planning_spec,
            capability_manifest=[{"id": "demo.collect"}, {"id": "demo.finish"}],
        )
        task, plan = planner.plan_and_create(objective="Do it", success_criteria=("Finished",))
        steps = self.store.list_steps(task.id)
        self.assertEqual(len(steps), 3)
        self.assertTrue(steps[0].metadata.get("internal_planning"))
        self.assertEqual(steps[0].status, "pass")
        self.assertEqual(steps[2].dependencies, (steps[1].id,))
        self.assertEqual(plan.steps[1].satisfies_criteria, (1,))
        executions = self.store.list_executions(task.id, step_id=steps[0].id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].provider, "planner")
        self.assertEqual(executions[0].status, "pass")
        self.assertTrue(any(a.kind == "task_plan" for a in self.store.list_artifacts(task.id)))

    def test_eval_score_can_change_model_route(self):
        registry = ProviderRegistry()
        a = FakeProvider(ProviderSpec("a", "a", "fake", {"reasoning.general": 0.9}, priority=50))
        b = FakeProvider(ProviderSpec("b", "b", "fake", {"reasoning.general": 0.8}, priority=50))
        registry.register(a)
        registry.register(b)
        router = ModelRouter(registry)
        spec = CapabilitySpec(id="reasoning.general", description="reason", executor_kind="model", verifier_id="core.nonempty")
        self.assertEqual(router.select(spec, context_chars=100).provider.spec.key, "a")
        router.record_eval_score("a", "reasoning.general", 0.5)
        router.record_eval_score("b", "reasoning.general", 0.95)
        self.assertEqual(router.select(spec, context_chars=100).provider.spec.key, "b")

    def test_model_abstention_fails_over_to_next_provider(self):
        class FailingProvider(FakeProvider):
            def generate(self, request):
                self.calls += 1
                raise RuntimeError("provider unavailable")
        providers = ProviderRegistry()
        first = FailingProvider(ProviderSpec("first", "m1", "fake", {"reasoning.general": 0.99}, priority=100))
        second = FakeProvider(ProviderSpec("second", "m2", "fake", {"reasoning.general": 0.90}, priority=90), text="recovered")
        providers.register(first)
        providers.register(second)
        caps = CapabilityRegistry()
        caps.register(CapabilitySpec(id="reasoning.general", description="reason", executor_kind="model", verifier_id="core.nonempty", budget=ExecutionBudget(max_attempts=3)))
        task = self._task()
        self.store.add_step(task.id, description="Reason", capability="reasoning.general", metadata={"accept_all_criteria": True})
        result = TaskRuntime(store=self.store, capabilities=caps, model_router=ModelRouter(providers)).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        executions = self.store.list_executions(task.id)
        self.assertEqual([x.provider for x in executions], ["first", "second"])
        self.assertEqual(executions[0].status, "abstain")
        self.assertEqual(executions[1].status, "pass")

    def test_long_task_depth_survives_many_bounded_frames(self):
        caps = CapabilityRegistry()
        caps.register(CapabilitySpec(id="deep.step", description="bounded frame", executor_kind="deterministic", verifier_id="core.nonempty"), lambda request: CapabilityOutcome("pass", output={"ordinal": request.attempt, "step": request.step_id}))
        criteria = tuple(f"criterion {i}" for i in range(1, 26))
        task = self._task(criteria=criteria)
        previous = None
        for i in range(1, 26):
            step = self.store.add_step(task.id, description=f"frame {i}", capability="deep.step", dependencies=([previous] if previous else []), metadata={"satisfies_criteria": [i]})
            previous = step.id
        runtime = TaskRuntime(store=self.store, capabilities=caps, budget=RuntimeBudget(max_executions=100, max_cycles=100))
        result = runtime.run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.store.list_executions(task.id)), 25)
        self.assertGreaterEqual(len([e for e in self.store.list_events(task.id) if e.name == "capability.completed"]), 25)

    def test_openai_responses_adapter_normalizes_output(self):
        provider = OpenAIResponsesProvider(ProviderSpec("openai", "gpt-test", "openai_responses", {"reasoning.general": 1.0}))
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}, clear=False), patch(
            "atlas_core.providers.http._post_json",
            return_value={"output_text": "hello", "usage": {"input_tokens": 3, "output_tokens": 2}},
        ):
            response = provider.generate(ModelRequest("reasoning.general", "system", "input"))
        self.assertEqual(response.text, "hello")
        self.assertEqual(response.metrics["output_tokens"], 2)

    def test_anthropic_messages_adapter_normalizes_output(self):
        provider = AnthropicMessagesProvider(ProviderSpec("anthropic", "claude-test", "anthropic_messages", {"reasoning.general": 1.0}))
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}, clear=False), patch(
            "atlas_core.providers.http._post_json",
            return_value={"content": [{"type": "text", "text": "hello"}], "usage": {"input_tokens": 3, "output_tokens": 2}},
        ):
            response = provider.generate(ModelRequest("reasoning.general", "system", "input"))
        self.assertEqual(response.text, "hello")
        self.assertEqual(response.metrics["input_tokens"], 3)

    def test_gemini_adapter_normalizes_output(self):
        provider = GeminiGenerateContentProvider(ProviderSpec("google", "gemini-test", "gemini_generate_content", {"reasoning.general": 1.0}))
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test"}, clear=False), patch(
            "atlas_core.providers.http._post_json",
            return_value={"candidates": [{"content": {"parts": [{"text": "hello"}]}}], "usageMetadata": {"promptTokenCount": 3}},
        ):
            response = provider.generate(ModelRequest("reasoning.general", "system", "input"))
        self.assertEqual(response.text, "hello")
        self.assertEqual(response.metrics["promptTokenCount"], 3)


if __name__ == "__main__":
    unittest.main()
