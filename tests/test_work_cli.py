from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas_core.__main__ import _work_runtime, main
from atlas_core.advanced.brief import TaskBrief
from atlas_core.work import WorkEngine, WorkRuntime


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

    def test_work_commands_do_not_use_build_runtime(self) -> None:
        self.assertIn("build_work_runtime", MAIN_SOURCE)
        self.assertIn('capabilities=("knowledge.ingest_text",)', MAIN_SOURCE)
        self.assertIn('capabilities=("knowledge.search",)', MAIN_SOURCE)
        self.assertIn('capabilities=("operations.morning_pack.generate",)', MAIN_SOURCE)
        self.assertNotIn("run_until_blocked", MAIN_SOURCE.split("def _run_work_command")[1])

    def test_index_text_and_search_run_through_work_runtime(self) -> None:
        with patch("atlas_core.__main__.build_runtime") as leftover:
            ingest_out = self._main("index-text", str(README_PATH), "--title", "README")
            leftover.assert_not_called()
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
            for artifact in runtime.store.list_artifacts(search["task_id"])
            if artifact.kind == "knowledge_search_results"
        ]
        self.assertTrue(hits)
        self.assertTrue(hits[0].payload["results"])
        self.assertIn("ContextBuilder", hits[0].payload["results"][0]["text"])
        contract = runtime.contract(search["task_id"])
        self.assertTrue(contract.capability("knowledge.search").armed)

    def test_morning_runs_through_work_runtime(self) -> None:
        with patch("atlas_core.__main__.build_runtime") as leftover:
            output = self._main(
                "morning",
                str(MORNING_EXPORT),
                "--config",
                str(MORNING_CONFIG),
                "--day",
                "2026-05-05",
            )
            leftover.assert_not_called()
        result = self._first_json_object(output)
        self.assertEqual(result["status"], "completed")
        self.assertIn("Machine / Item", output)
        runtime = _work_runtime(self.db, morning=True)
        packs = [
            artifact
            for artifact in runtime.store.list_artifacts(result["task_id"])
            if artifact.kind == "morning_pack"
        ]
        self.assertEqual(len(packs), 1)
        self.assertTrue(packs[0].payload["markdown"].strip())
        self.assertEqual(runtime.get(result["task_id"]).status, "completed")

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
