from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import CapabilityOutcome
from atlas_core.work import (
    UnavailableWork,
    CapabilityExecutionProfile,
    DeploymentInventory,
    build_work_runtime,
)


def _pass(_request):
    return CapabilityOutcome("pass", output={"ok": True}, receipt={"ok": True})


class WorkAvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas-work.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _text_knowledge_runtime(self):
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.ingest_text",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _pass,
        )
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.search",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _pass,
        )
        return build_work_runtime(db_path=self.db, profiles=inventory)

    def test_pdf_manual_index_is_unavailable_without_pdf_ingest(self) -> None:
        runtime = self._text_knowledge_runtime()
        with self.assertRaises(UnavailableWork) as ctx:
            runtime.accept(
                TaskBrief(
                    objective="Index the attached PDF manual into Atlas knowledge",
                    capabilities=("documents.multimodal", "knowledge.ingest_text"),
                    required_authority="modify_internal",
                    expected_effect="Index local knowledge",
                ),
                "modify_internal",
            )
        result = ctx.exception.result
        self.assertEqual(result.status, "unavailable")
        self.assertIn("PDF ingestion", result.reason)
        self.assertEqual(result.unarmed, ("documents.multimodal",))
        self.assertEqual(
            result.capabilities,
            ("documents.multimodal", "knowledge.ingest_text"),
        )
        self.assertEqual(runtime.store.list_work(), ())
        self.assertEqual(_work_residue(self.db), {})

    def test_armed_search_still_accepts(self) -> None:
        runtime = self._text_knowledge_runtime()
        work_id = runtime.accept(
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
            inputs={"knowledge.search": {"query": "torque", "limit": 1}},
        )
        self.assertEqual(runtime.get(work_id).status, "planned")
        self.assertTrue(runtime.contract(work_id).capability("knowledge.search").armed)


def _work_residue(path: Path) -> dict[str, int]:
    tables = (
        "work",
        "work_executions",
        "work_confirmations",
        "work_events",
        "work_contracts",
        "work_steps",
        "work_artifacts",
        "work_approvals",
    )
    leftover: dict[str, int] = {}
    if not path.is_file():
        return leftover
    with sqlite3.connect(path) as db:
        names = {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in tables:
            if table not in names:
                continue
            row = db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            if int(row[0]):
                leftover[table] = int(row[0])
    return leftover
