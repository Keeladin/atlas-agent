from __future__ import annotations
from tests.capability_fixtures import make_registration, register_cap

import tempfile
import unittest
from pathlib import Path

from atlas_core.capabilities import (
    CapabilityOutcome,
    CapabilityRegistry,
    
    ExecutionBudget,
)
from atlas_core.context import ContextBuilder
from atlas_core.deliverable import (
    check_deliverable,
    classify_output,
    infer_deliverable,
    infer_presentation_profile,
)
from atlas_core.presentation import TaskPresenter
from atlas_core.providers import ModelResponse, ModelRouter, ProviderRegistry, ProviderSpec
from atlas_core.runtime import TaskRuntime
from atlas_core.tasks import TaskStore
from atlas_core.verification import (
    CompletionVerifier,
    OutcomeGate,
    SemanticOutcomeVerifier,
    VerifierRegistry,
)


ANALYSIS_ARTIFACT = """### Evidence
* User Objective: To receive a short story about a man finding a magic pond with water of immortality.
* Success Criteria: The narrative must be "believable."
* System Constraints: Investigate before concluding. Separate evidence, uncertainty and inference.
### Uncertainty
* Definition of "Believability": In the context of fantasy fiction, believable is subjective.
* Current Artifact Status: There is no external evidence supplied within the context to analyze.
"""

STORY_ARTIFACT = """
Marek walked the same ridge path every morning for eleven years, counting fence posts
so he would not have to count the years. After the rain the ground gave way beside
the third post, and he slid into a hollow no wider than a cattle trough.

The water there was still and clear enough to show his own face, older than he liked.
A carved stone at the rim said the pool would keep a man from dying. Marek cupped a
handful, thought of his father coughing through a winter, and let the water run back
through his fingers. He climbed out muddy and thirsty and went home to make soup.
"""


class RecordingProvider:
    def __init__(self, spec, text="model result"):
        self.spec = spec
        self.text = text
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if request.metadata.get("purpose") == "outcome_verification":
            return ModelResponse(
                '{"status":"pass","requested_type":"narrative","produced_type":"narrative","summary":"story present"}',
                self.spec.key,
                self.spec.model,
                {},
                {"output_tokens": 8},
            )
        return ModelResponse(self.text, self.spec.key, self.spec.model, {}, {"output_tokens": 10})


class FlipToStoryProvider(RecordingProvider):
    def generate(self, request):
        self.requests.append(request)
        if request.metadata.get("purpose") == "outcome_verification":
            return ModelResponse(
                '{"status":"pass","requested_type":"narrative","produced_type":"narrative","summary":"story present"}',
                self.spec.key,
                self.spec.model,
                {},
                {"output_tokens": 8},
            )
        produces = [
            item
            for item in self.requests
            if item.metadata.get("purpose") != "outcome_verification"
        ]
        text = ANALYSIS_ARTIFACT if len(produces) == 1 else STORY_ARTIFACT
        return ModelResponse(text, self.spec.key, self.spec.model, {}, {"output_tokens": 10})


class OutcomeValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tmp.name) / "atlas.db")
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_story_objective_is_a_narrative_contract(self):
        contract = infer_deliverable(
            "tel me a short story about a guy who found a magic pond with water of immortality",
            ("make it believable",),
        )
        self.assertEqual(contract.kind, "narrative")
        self.assertEqual(contract.requested, "short story")
        self.assertFalse(infer_deliverable("Implement the user story in Jira", ("Done",)).kind == "narrative")
        self.assertEqual(infer_deliverable("Implement the user story in Jira", ("Done",)).kind, "code")

    def test_analysis_report_is_not_a_story(self):
        contract = infer_deliverable("Tell me a short story about a magic pond", ("make it believable",))
        ok, summary, produced = check_deliverable(contract, ANALYSIS_ARTIFACT)
        self.assertFalse(ok)
        self.assertEqual(produced, "analysis")
        self.assertIn("analysis", summary)
        self.assertTrue(check_deliverable(contract, STORY_ARTIFACT)[0])
        self.assertEqual(classify_output(ANALYSIS_ARTIFACT), "analysis")
        self.assertEqual(classify_output(STORY_ARTIFACT), "narrative")

    def test_story_request_does_not_complete_on_analysis_artifact(self):
        provider = RecordingProvider(
            ProviderSpec("local", "atlas", "fake", {"reasoning.general": 1.0}),
            text=ANALYSIS_ARTIFACT,
        )
        providers = ProviderRegistry()
        providers.register(provider)
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
            objective="tel me a short story about a guy who found a magic pond with water of immortality",
            success_criteria=("make it believable",),
            authority_scope="interpret",
        )
        self.store.add_step(
            task.id,
            description="Write the story",
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
        self.assertTrue(
            any("analysis" in (item.error or "") for item in self.store.list_executions(task.id))
        )
        presentation = TaskPresenter(self.store).build(task.id)
        self.assertIn("analysis", presentation.failure_reason or "")
        self.assertNotIn("Marek", presentation.render_markdown())
        produce_calls = [
            request
            for request in provider.requests
            if request.metadata.get("purpose") != "outcome_verification"
        ]
        self.assertGreaterEqual(len(produce_calls), 2)
        self.assertTrue(
            any("Produce the requested artifact itself" in request.system for request in produce_calls)
        )

    def test_story_request_retries_then_accepts_the_story(self):
        provider = FlipToStoryProvider(
            ProviderSpec("local", "atlas", "fake", {"reasoning.general": 1.0})
        )
        providers = ProviderRegistry()
        providers.register(provider)
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
            objective="Tell me a short story about a man who found a magic pond",
            success_criteria=("make it believable",),
            authority_scope="interpret",
        )
        self.store.add_step(
            task.id,
            description="Write the story",
            capability="reasoning.general",
            metadata={"satisfies_criteria": [1]},
        )
        result = TaskRuntime(
            store=self.store,
            capabilities=capabilities,
            model_router=ModelRouter(providers),
        ).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.store.list_criteria(task.id)[0].status, "accepted")
        presentation = TaskPresenter(self.store).build(task.id)
        self.assertIn("Marek", presentation.render_markdown())
        rework = [
            item
            for item in self.store.list_executions(task.id)
            if item.status == "rework"
        ]
        self.assertEqual(len(rework), 1)
        self.assertIn("analysis", rework[0].error or "")

    def test_context_for_a_story_does_not_use_the_research_profile(self):
        task = self.store.create_task(
            objective="Write a short story about a magic pond",
            success_criteria=("make it believable",),
        )
        step = self.store.add_step(
            task.id,
            description="Write the story",
            capability="reasoning.general",
        )
        spec = make_registration(
            id="reasoning.general",
            description="reason",
            executor_kind="model",
            context_profile="research",
            verifier_id="core.nonempty",
        )
        pack = ContextBuilder(self.store).build(
            task.id,
            step.id,
            artifact_ids=(),
            registration=spec,
        )
        self.assertEqual(pack.payload["deliverable_contract"]["kind"], "narrative")
        self.assertEqual(pack.payload["context_profile"]["name"], "compose")
        self.assertIn("Produce the requested artifact itself", pack.payload["context_profile"]["instruction"])
        self.assertNotIn("Investigate before concluding", pack.payload["context_profile"]["instruction"])
        self.assertIn("Do not substitute analysis", pack.payload["system"]["evidence_rule"])
        self.assertEqual(pack.payload["capability_profile"], "research")
        self.assertEqual(pack.payload["presentation_profile"], "compose")

    def test_factual_question_does_not_use_the_research_profile(self):
        task = self.store.create_task(
            objective="what is the golden ratio",
            success_criteria=("Produce a truthful answer.",),
        )
        step = self.store.add_step(
            task.id,
            description="Answer the question",
            capability="reasoning.general",
        )
        spec = make_registration(
            id="reasoning.general",
            description="reason",
            executor_kind="model",
            context_profile="research",
            verifier_id="core.nonempty",
        )
        pack = ContextBuilder(self.store).build(
            task.id,
            step.id,
            artifact_ids=(),
            registration=spec,
        )
        self.assertEqual(infer_deliverable(task.objective, task.success_criteria).kind, "answer")
        self.assertEqual(infer_presentation_profile(task.objective, task.success_criteria), "answer")
        self.assertEqual(pack.payload["deliverable_contract"]["kind"], "answer")
        self.assertEqual(pack.payload["capability_profile"], "research")
        self.assertEqual(pack.payload["presentation_profile"], "answer")
        self.assertEqual(pack.payload["context_profile"]["name"], "answer")
        self.assertIn("directly and concisely", pack.payload["context_profile"]["instruction"])
        self.assertNotIn("Investigate before concluding", pack.payload["context_profile"]["instruction"])
        self.assertNotIn("Separate evidence, uncertainty and inference", pack.payload["context_profile"]["instruction"])
        ok, _, produced = check_deliverable(
            infer_deliverable(task.objective, task.success_criteria),
            "The golden ratio is (1 + sqrt(5)) / 2, about 1.618.",
        )
        self.assertTrue(ok)
        self.assertEqual(produced, "prose")
        self.assertFalse(check_deliverable(infer_deliverable(task.objective), ANALYSIS_ARTIFACT)[0])

    def test_presentation_profile_follows_intent_not_capability(self):
        self.assertEqual(infer_presentation_profile("hello"), "conversational")
        self.assertEqual(infer_presentation_profile("what is ohms law"), "answer")
        self.assertEqual(
            infer_presentation_profile("analyze the trade-offs of SQLite versus Postgres"),
            "evidence",
        )
        self.assertEqual(
            infer_presentation_profile("Give me a thorough analysis of Atlas runtime governance"),
            "research",
        )
        self.assertEqual(
            infer_presentation_profile("Should I take this investment advice for retirement?"),
            "evidence",
        )
        self.assertEqual(
            infer_presentation_profile("Write a short story about a magic pond"),
            "compose",
        )
        self.assertEqual(infer_deliverable("hello").kind, "conversation")
        self.assertEqual(infer_deliverable("what is ohms law").kind, "answer")
        self.assertEqual(infer_deliverable("Implement a function to parse JSON").kind, "code")

    def test_completion_rejects_accepted_analysis_evidence_for_a_story(self):
        task = self.store.create_task(
            objective="Tell me a short story about a magic pond",
            success_criteria=("make it believable",),
        )
        step = self.store.add_step(
            task.id,
            description="Write the story",
            capability="reasoning.general",
        )
        artifact = self.store.put_artifact(
            task.id,
            step_id=step.id,
            kind="capability_result",
            payload=ANALYSIS_ARTIFACT,
        )
        self.store.set_step_status(step.id, "running")
        self.store.set_step_status(step.id, "pass")
        self.store.set_criterion_status(
            self.store.list_criteria(task.id)[0].id,
            "accepted",
            evidence_artifact_ids=(artifact.id,),
        )
        decision = CompletionVerifier(self.store).evaluate(task.id)
        self.assertFalse(decision.complete)
        self.assertEqual(decision.status, "failed")
        self.assertTrue(any("deliverable" in reason for reason in decision.reasons))

    def test_completion_failure_fails_the_task_instead_of_waiting(self):
        capabilities = CapabilityRegistry()
        capabilities.register(
            make_registration(
                id="demo.pass",
                description="pass",
                executor_kind="deterministic",
                verifier_id="core.nonempty",
            ),
            lambda request: CapabilityOutcome("pass", output={"ok": True}),
        )
        task = self.store.create_task(
            objective="Tell me a short story about a magic pond",
            success_criteria=("make it believable",),
        )
        # Intermediate step does not claim criteria, so the outcome gate lets it pass.
        # Completion still refuses because no narrative evidence exists.
        self.store.add_step(task.id, description="Prep", capability="demo.pass")
        result = TaskRuntime(store=self.store, capabilities=capabilities).run_until_blocked(task.id)
        self.assertEqual(result.status, "waiting")
        self.store.set_criterion_status(
            self.store.list_criteria(task.id)[0].id,
            "accepted",
            evidence_artifact_ids=(
                [item.id for item in self.store.list_artifacts(task.id) if item.kind == "capability_result"][0],
            ),
        )
        self.store.set_task_status(task.id, "active")
        second = TaskRuntime(store=self.store, capabilities=capabilities).run_until_blocked(task.id)
        self.assertEqual(second.status, "failed")

    def test_semantic_verifier_can_reject_after_type_check_passes(self):
        class Provider:
            spec = ProviderSpec("v", "v", "fake", {"reasoning.general": 1.0})

            def generate(self, request):
                return ModelResponse(
                    '{"status":"rework","requested_type":"narrative","produced_type":"narrative","summary":"story is not believable"}',
                    "v",
                    "v",
                    {},
                    {},
                )

        class Router:
            def select(self, spec, *, context_chars, exclude_provider_keys=(), **kwargs):
                class Route:
                    provider = Provider()

                return Route()

        gate = OutcomeGate(semantic=SemanticOutcomeVerifier(Router()))
        spec = make_registration(
            id="reasoning.general",
            description="reason",
            executor_kind="model",
            verifier_id="core.nonempty",
        )
        task = self.store.create_task(
            objective="Tell me a short story about a pond",
            success_criteria=("make it believable",),
        )
        step = self.store.add_step(
            task.id,
            description="Write",
            capability="reasoning.general",
            metadata={"satisfies_criteria": [1]},
        )
        result = gate.evaluate(
            profile=spec.profile,
            output=STORY_ARTIFACT,
            context={},
            step=step,
            task=task,
        )
        self.assertEqual(result.status, "rework")
        self.assertIn("believable", result.summary)
        self.assertEqual(result.details.get("layer"), "semantic")
        self.assertEqual(result.details.get("semantic", {}).get("status"), "rework")

    def _semantic_gate(self, verdict_text: str) -> OutcomeGate:
        class Provider:
            spec = ProviderSpec("v", "v", "fake", {"reasoning.general": 1.0})

            def generate(self, request):
                return ModelResponse(verdict_text, "v", "v", {}, {})

        class Router:
            def select(self, spec, *, context_chars, exclude_provider_keys=(), **kwargs):
                class Route:
                    provider = Provider()

                return Route()

        return OutcomeGate(semantic=SemanticOutcomeVerifier(Router()))

    def test_semantic_pass_is_recorded_on_the_gate(self):
        gate = self._semantic_gate(
            '{"status":"pass","requested_type":"narrative","produced_type":"narrative","summary":"story present"}'
        )
        spec = make_registration(
            id="reasoning.general",
            description="reason",
            executor_kind="model",
            verifier_id="core.nonempty",
        )
        task = self.store.create_task(
            objective="Tell me a short story about a pond",
            success_criteria=("make it believable",),
        )
        step = self.store.add_step(
            task.id,
            description="Write",
            capability="reasoning.general",
            metadata={"satisfies_criteria": [1]},
        )
        result = gate.evaluate(profile=spec.profile, output=STORY_ARTIFACT, context={}, step=step, task=task)
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.details.get("layer"), "semantic")
        self.assertEqual(result.details["semantic"]["status"], "pass")
        self.assertIn("story present", result.summary)

    def test_quality_abstain_does_not_pass_the_gate(self):
        gate = self._semantic_gate("not a verdict")
        spec = make_registration(
            id="reasoning.general",
            description="reason",
            executor_kind="model",
            verifier_id="core.nonempty",
        )
        task = self.store.create_task(
            objective="Tell me a short story about a pond",
            success_criteria=("make it believable",),
        )
        step = self.store.add_step(
            task.id,
            description="Write",
            capability="reasoning.general",
            metadata={"satisfies_criteria": [1]},
        )
        result = gate.evaluate(profile=spec.profile, output=STORY_ARTIFACT, context={}, step=step, task=task)
        self.assertEqual(result.status, "abstain")
        self.assertEqual(result.details.get("layer"), "semantic")
        self.assertEqual(result.details["semantic"]["status"], "abstain")
        self.assertIn("unusable JSON", result.summary)

    def test_semantic_abstain_without_quality_still_passes_type_check(self):
        gate = self._semantic_gate("not a verdict")
        spec = make_registration(
            id="reasoning.general",
            description="reason",
            executor_kind="model",
            verifier_id="core.nonempty",
        )
        task = self.store.create_task(
            objective="Tell me a short story about a pond",
            success_criteria=("Finished",),
        )
        step = self.store.add_step(
            task.id,
            description="Write",
            capability="reasoning.general",
            metadata={"satisfies_criteria": [1]},
        )
        result = gate.evaluate(profile=spec.profile, output=STORY_ARTIFACT, context={}, step=step, task=task)
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.details["semantic"]["status"], "abstain")
        self.assertEqual(result.details.get("layer"), "deterministic")

    def test_quality_abstain_does_not_complete_or_accept_criteria(self):
        class Provider:
            def __init__(self):
                self.spec = ProviderSpec("local", "atlas", "fake", {"reasoning.general": 1.0})

            def generate(self, request):
                if request.metadata.get("purpose") == "outcome_verification":
                    return ModelResponse("not a verdict", self.spec.key, self.spec.model, {}, {})
                return ModelResponse(STORY_ARTIFACT, self.spec.key, self.spec.model, {}, {"output_tokens": 10})

        provider = Provider()
        providers = ProviderRegistry()
        providers.register(provider)
        capabilities = CapabilityRegistry()
        capabilities.register(
            make_registration(
                id="reasoning.general",
                description="reason",
                executor_kind="model",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=1),
            )
        )
        task = self.store.create_task(
            objective="Tell me a short story about a man who found a magic pond",
            success_criteria=("make it believable",),
            authority_scope="interpret",
        )
        self.store.add_step(
            task.id,
            description="Write the story",
            capability="reasoning.general",
            metadata={"satisfies_criteria": [1]},
        )
        result = TaskRuntime(
            store=self.store,
            capabilities=capabilities,
            model_router=ModelRouter(providers),
        ).run_until_blocked(task.id)
        self.assertNotEqual(result.status, "completed")
        self.assertEqual(self.store.list_criteria(task.id)[0].status, "pending")
        executions = self.store.list_executions(task.id)
        self.assertTrue(any(item.status == "abstain" for item in executions))
        verdicts = [
            artifact
            for artifact in self.store.list_artifacts(task.id)
            if artifact.kind == "verification_result"
        ]
        self.assertTrue(verdicts)
        payload = verdicts[-1].payload
        self.assertEqual(payload["status"], "abstain")
        self.assertEqual(payload["details"]["outcome_gate"]["semantic"]["status"], "abstain")
        presentation = TaskPresenter(self.store).build(task.id)
        self.assertNotEqual(presentation.status, "completed")

    def test_semantic_pass_is_persisted_on_a_quality_story(self):
        provider = RecordingProvider(
            ProviderSpec("local", "atlas", "fake", {"reasoning.general": 1.0}),
            text=STORY_ARTIFACT,
        )
        providers = ProviderRegistry()
        providers.register(provider)
        capabilities = CapabilityRegistry()
        capabilities.register(
            make_registration(
                id="reasoning.general",
                description="reason",
                executor_kind="model",
                verifier_id="core.nonempty",
                budget=ExecutionBudget(max_attempts=1),
            )
        )
        task = self.store.create_task(
            objective="Tell me a short story about a man who found a magic pond",
            success_criteria=("make it believable",),
            authority_scope="interpret",
        )
        self.store.add_step(
            task.id,
            description="Write the story",
            capability="reasoning.general",
            metadata={"satisfies_criteria": [1]},
        )
        result = TaskRuntime(
            store=self.store,
            capabilities=capabilities,
            model_router=ModelRouter(providers),
        ).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.store.list_criteria(task.id)[0].status, "accepted")
        verdicts = [
            artifact
            for artifact in self.store.list_artifacts(task.id)
            if artifact.kind == "verification_result"
        ]
        self.assertTrue(verdicts)
        gate = verdicts[-1].payload["details"]["outcome_gate"]
        self.assertEqual(gate["layer"], "semantic")
        self.assertEqual(gate["semantic"]["status"], "pass")

    def test_core_deliverable_verifier_is_registered(self):
        registry = VerifierRegistry()
        result = registry.verify(
            "core.deliverable",
            make_registration(
                id="reasoning.general",
                description="reason",
                executor_kind="model",
                verifier_id="core.deliverable",
            ).profile,
            ANALYSIS_ARTIFACT,
            {
                "task": {
                    "objective": "Write a short story about a pond",
                    "success_criteria": ["make it believable"],
                }
            },
        )
        self.assertEqual(result.status, "rework")
        self.assertIn("analysis", result.summary)


if __name__ == "__main__":
    unittest.main()
