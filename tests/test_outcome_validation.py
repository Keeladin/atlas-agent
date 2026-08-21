from __future__ import annotations
from tests.capability_fixtures import make_registration

import tempfile
import unittest
from pathlib import Path

from atlas_core.context import ContextBuilder
from atlas_core.deliverable import (
    check_deliverable,
    classify_output,
    infer_deliverable,
    infer_presentation_profile,
)
from atlas_core.tasks import TaskStore
from atlas_core.verification import CompletionVerifier, VerifierRegistry


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
