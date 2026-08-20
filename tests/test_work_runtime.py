from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import CapabilityBinding, CapabilityOutcome
from atlas_core.runtime import RuntimeResult, TaskRuntime
from atlas_core.work import (
    UNAVAILABLE,
    CapabilityExecutionProfile,
    ExecutionProfileIndex,
    WorkError,
    WorkRuntime,
    build_work_runtime,
)


def _brief(**overrides) -> TaskBrief:
    payload = dict(
        objective="Create automation",
        capabilities=("automation.workflow.create",),
        required_authority="execute_external",
        expected_effect="Create workflow automation",
    )
    payload.update(overrides)
    return TaskBrief(**payload)


class WorkRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work_db = self.root / "atlas-work.db"
        self.chat_db = self.root / "atlas-chat.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _runtime(self) -> WorkRuntime:
        return build_work_runtime(db_path=self.work_db)

    def test_accept_brief_creates_work_not_chat(self) -> None:
        runtime = self._runtime()
        work_id = runtime.accept(_brief(), "execute_external")
        record = runtime.get(work_id)
        self.assertEqual(record.objective, "Create automation")
        self.assertEqual(record.capabilities, ("automation.workflow.create",))
        self.assertEqual(record.authority_scope, "execute_external")
        self.assertEqual(record.status, "planned")
        frame = runtime.frame(work_id)
        self.assertEqual(frame.work_id, work_id)
        self.assertEqual(frame.capabilities, ("automation.workflow.create",))
        self.assertEqual(frame.bindings, ())
        self.assertEqual(frame.allowed_tools, ())
        self.assertEqual(frame.confirmation_requirements, ("automation.workflow.create",))
        self.assertTrue(self.work_db.is_file())
        self.assertFalse(self.chat_db.exists())
        tables = _table_names(self.work_db)
        self.assertIn("tasks", tables)
        self.assertIn("task_steps", tables)
        self.assertIn("task_executions", tables)
        self.assertIn("task_artifacts", tables)
        self.assertIn("task_claims", tables)
        self.assertIn("task_approvals", tables)
        self.assertIn("task_events", tables)
        self.assertIn("task_checkpoints", tables)
        self.assertNotIn("conversations", tables)
        self.assertNotIn("conversation_turns", tables)
        kinds = {
            artifact.kind
            for artifact in runtime._engine.store.list_artifacts(work_id)
        }
        self.assertIn("task_brief", kinds)
        self.assertIn("runtime_frame", kinds)

    def test_run_without_profile_does_not_execute(self) -> None:
        runtime = self._runtime()
        work_id = runtime.accept(_brief(), "execute_external")
        engine = runtime._engine
        calls: list[str] = []
        original = engine.run_until_blocked

        def wrapped(task_id: str):
            calls.append(task_id)
            return original(task_id)

        engine.run_until_blocked = wrapped  # type: ignore[method-assign]
        result = runtime.run(work_id)
        self.assertEqual(calls, [])
        self.assertEqual(result.reason, UNAVAILABLE)
        self.assertEqual(result.executions, 0)
        self.assertEqual(engine.store.list_executions(work_id), ())
        self.assertEqual(runtime.get(work_id).status, "planned")

    def test_run_with_profile_passes_through_task_runtime(self) -> None:
        profiles = ExecutionProfileIndex()
        profiles.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                implementation=CapabilityBinding(
                    "automation.workflow.create", "internal", "record", "1"
                ),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            lambda request: CapabilityOutcome(
                "pass",
                output={"capability": request.capability_id},
                receipt={"ok": True},
            ),
        )
        runtime = build_work_runtime(db_path=self.work_db, profiles=profiles)
        work_id = runtime.accept(_brief(), "execute_external")
        engine = runtime._engine
        self.assertIsInstance(engine, TaskRuntime)
        calls: list[str] = []
        original = engine.run_until_blocked

        def wrapped(task_id: str):
            calls.append(task_id)
            return original(task_id)

        engine.run_until_blocked = wrapped  # type: ignore[method-assign]
        result = runtime.run(work_id)
        self.assertEqual(calls, [work_id])
        self.assertIsInstance(result, RuntimeResult)
        self.assertEqual(result.task_id, work_id)
        self.assertGreaterEqual(result.executions, 1)
        self.assertEqual(result.status, "completed")
        executions = engine.store.list_executions(work_id)
        self.assertTrue(executions)
        self.assertEqual(executions[-1].status, "pass")
        self.assertEqual(runtime.get(work_id).status, "completed")

    def test_chat_database_is_untouched(self) -> None:
        self.chat_db.write_text("chat-sentinel", encoding="utf-8")
        before = self.chat_db.read_bytes()
        runtime = self._runtime()
        work_id = runtime.accept(_brief(), "execute_external")
        runtime.run(work_id)
        self.assertEqual(self.chat_db.read_bytes(), before)
        self.assertNotEqual(self.work_db, self.chat_db)
        self.assertTrue(self.work_db.is_file())

    def test_build_work_runtime_is_the_constructor(self) -> None:
        runtime = build_work_runtime(db_path=self.work_db)
        self.assertIsInstance(runtime, WorkRuntime)
        self.assertIsInstance(runtime._engine, TaskRuntime)

    def test_insufficient_authority_is_rejected(self) -> None:
        runtime = self._runtime()
        with self.assertRaises(WorkError) as ctx:
            runtime.accept(_brief(), "read")
        self.assertIn("required_authority", str(ctx.exception))
        self.assertEqual(runtime._engine.store.list_tasks(), ())


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {str(row[0]) for row in rows}
