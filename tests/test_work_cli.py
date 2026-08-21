from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas_core.__main__ import _work_runtime, main
from atlas_core.advanced.brief import TaskBrief
from atlas_core.knowledge import source_content_sha256
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    WorkEngine,
    WorkRuntime,
    build_work_runtime,
)


ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
MORNING_EXPORT = ROOT / "tests" / "fixtures" / "export_excerpt.txt"
MORNING_CONFIG = ROOT / "config" / "v1.json"
MAIN_SOURCE = (ROOT / "atlas_core" / "__main__.py").read_text(encoding="utf-8")


class WorkCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas-work.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _main(self, *argv: str) -> str:
        buf = io.StringIO()
        with (
            patch("sys.argv", ["atlas_core", "--db", str(self.db), *argv]),
            patch("sys.stdout", buf),
        ):
            main()
        return buf.getvalue()

    def _first_json_object(self, output: str) -> dict:
        start = output.find("{")
        self.assertGreaterEqual(start, 0)
        decoder = json.JSONDecoder()
        payload, _end = decoder.raw_decode(output[start:])
        return payload

    def test_work_commands_do_not_use_legacy_engine(self) -> None:
        self.assertIn("build_work_runtime", MAIN_SOURCE)
        self.assertIn('capabilities=("knowledge.ingest_text",)', MAIN_SOURCE)
        self.assertIn('capabilities=("knowledge.search",)', MAIN_SOURCE)
        self.assertIn('capabilities=("operations.morning_pack.generate",)', MAIN_SOURCE)
        self.assertNotIn("build_runtime", MAIN_SOURCE)
        self.assertNotIn("TaskPlanner", MAIN_SOURCE)
        self.assertNotIn("TaskRuntime", MAIN_SOURCE)
        self.assertNotIn("CapabilityRegistry", MAIN_SOURCE)
        self.assertNotIn("run_until_blocked", MAIN_SOURCE)
        self.assertNotIn('add_parser("plan"', MAIN_SOURCE)
        self.assertNotIn("--providers", MAIN_SOURCE)

    def test_index_text_and_search_run_through_work_runtime(self) -> None:
        ingest_out = self._main("index-text", str(README_PATH), "--title", "README")
        ingest = self._first_json_object(ingest_out)
        self.assertEqual(ingest["status"], "completed")
        self.assertGreaterEqual(ingest["executions"], 1)
        search_out = self._main("search", "ContextBuilder")
        search = self._first_json_object(search_out)
        self.assertEqual(search["status"], "completed")
        self.assertIn("## Retrieved sources", search_out)
        runtime = _work_runtime(self.db, knowledge=True)
        self.assertIsInstance(runtime, WorkRuntime)
        self.assertIsInstance(runtime._engine, WorkEngine)
        hits = [
            artifact
            for artifact in runtime.store.list_artifacts(search["work_id"])
            if artifact.kind == "knowledge_search_results"
        ]
        self.assertTrue(hits)
        self.assertTrue(hits[0].payload["results"])
        self.assertIn("ContextBuilder", hits[0].payload["results"][0]["text"])
        contract = runtime.contract(search["work_id"])
        self.assertTrue(contract.capability("knowledge.search").armed)

    def test_morning_runs_through_work_runtime(self) -> None:
        output = self._main(
            "morning",
            str(MORNING_EXPORT),
            "--config",
            str(MORNING_CONFIG),
            "--day",
            "2026-05-05",
        )
        result = self._first_json_object(output)
        self.assertEqual(result["status"], "completed")
        self.assertIn("Machine / Item", output)
        runtime = _work_runtime(self.db, morning=True)
        packs = [
            artifact
            for artifact in runtime.store.list_artifacts(result["work_id"])
            if artifact.kind == "morning_pack"
        ]
        self.assertEqual(len(packs), 1)
        self.assertTrue(packs[0].payload["markdown"].strip())
        self.assertEqual(runtime.get(result["work_id"]).status, "completed")

    def test_search_then_answer_kind_match_on_work_runtime(self) -> None:
        self._main("index-text", str(README_PATH), "--title", "README")
        runtime = _work_runtime(self.db, knowledge=True)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search then answer",
                capabilities=("knowledge.search", "knowledge.answer"),
                required_authority="read",
                expected_effect="A grounded local answer",
            ),
            "read",
            inputs={"knowledge.search": {"query": "ContextBuilder", "limit": 5}},
        )
        by_capability = {
            step.capability: step for step in runtime.store.list_steps(work_id)
        }
        self.assertEqual(
            by_capability["knowledge.answer"].dependencies,
            (by_capability["knowledge.search"].id,),
        )
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        answers = [
            artifact
            for artifact in runtime.store.list_artifacts(work_id)
            if artifact.kind == "grounded_answer"
        ]
        self.assertTrue(answers)
        self.assertIn("From retrieved sources", answers[0].payload)

    def test_plan_is_not_a_cli_command(self) -> None:
        buf = io.StringIO()
        err = io.StringIO()
        with (
            patch("sys.argv", ["atlas_core", "--db", str(self.db), "plan", "Explain Atlas"]),
            patch("sys.stdout", buf),
            patch("sys.stderr", err),
            self.assertRaises(SystemExit),
        ):
            main()
        self.assertIn("invalid choice", err.getvalue())

    def test_status_tasks_result_run_and_cancel_use_work_persistence(self) -> None:
        ingest = self._first_json_object(
            self._main("index-text", str(README_PATH), "--title", "README")
        )
        self.assertEqual(ingest["status"], "completed")
        listed = self._main("work")
        self.assertIn(ingest["work_id"], listed)
        status = self._first_json_object(self._main("status", ingest["work_id"]))
        self.assertEqual(status["work"]["id"], ingest["work_id"])
        self.assertEqual(status["work"]["status"], "completed")
        report = self._main("result", ingest["work_id"])
        self.assertIn(ingest["work_id"], report)
        runtime = _work_runtime(self.db, knowledge=True)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search Atlas knowledge for: ContextBuilder",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="A source-grounded local knowledge search result is produced.",
            ),
            "read",
            inputs={"knowledge.search": {"query": "ContextBuilder", "limit": 5}},
        )
        ran = self._first_json_object(self._main("run", work_id))
        self.assertEqual(ran["status"], "completed")
        self.assertEqual(ran["work_id"], work_id)
        pending_id = runtime.accept(
            TaskBrief(
                objective="Search Atlas knowledge for: later cancel",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="A source-grounded local knowledge search result is produced.",
            ),
            "read",
            inputs={"knowledge.search": {"query": "later cancel", "limit": 1}},
        )
        cancelled = self._main("cancel", pending_id)
        self.assertIn("cancelled", cancelled)
        self.assertEqual(runtime.get(pending_id).status, "cancelled")

    def test_recover_approve_and_deny_use_work_runtime(self) -> None:
        runtime = _work_runtime(self.db, knowledge=True)
        text = README_PATH.read_text(encoding="utf-8")
        work_id = runtime.accept(
            TaskBrief(
                objective=f"Index local knowledge source {README_PATH.name}",
                capabilities=("knowledge.ingest_text",),
                required_authority="modify_internal",
                expected_effect="The source is durably indexed with chunk provenance.",
            ),
            "modify_internal",
            inputs={
                "knowledge.ingest_text": {
                    "title": "README",
                    "source_path": str(README_PATH.resolve()),
                    "source_uri": str(README_PATH.resolve()),
                    "content_sha256": source_content_sha256(text),
                    "byte_size": README_PATH.stat().st_size,
                    "chunk_chars": 4000,
                    "overlap_chars": 400,
                }
            },
        )
        store = runtime.store
        step = store.list_steps(work_id)[0]
        store.set_work_status(work_id, "active")
        store.begin_execution(
            work_id,
            step_id=step.id,
            capability="knowledge.ingest_text",
            capability_version=step.capability_version or "1.0.0",
        )
        recovered = self._first_json_object(self._main("recover", work_id))
        self.assertEqual(recovered["work_id"], work_id)
        self.assertEqual(recovered["recovered"], 1)
        self.assertEqual(recovered["failed_closed"], 0)
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                executor_kind="human",
                verification_required=False,
            )
        )
        human = build_work_runtime(db_path=self.db, profiles=inventory)
        approved_id = human.accept(
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        self.assertEqual(human.run(approved_id).status, "waiting")
        approval = human.store.list_approvals(approved_id, status="pending")[0]
        approved = self._first_json_object(
            self._main("approve", approval.id, "--note", "go")
        )
        self.assertEqual(approved["approval_id"], approval.id)
        self.assertEqual(approved["status"], "approved")
        denied_id = human.accept(
            TaskBrief(
                objective="Deny automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        self.assertEqual(human.run(denied_id).status, "waiting")
        denied_approval = human.store.list_approvals(denied_id, status="pending")[0]
        denied = self._first_json_object(
            self._main("deny", denied_approval.id, "--note", "no")
        )
        self.assertEqual(denied["status"], "denied")
