from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import CapabilityBinding, CapabilityOutcome
from atlas_core.runtime import RuntimeResult, TaskRuntime
from atlas_core.tasks import TaskStoreError
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
        contract = runtime.contract(work_id)
        self.assertEqual(contract.work_id, work_id)
        self.assertEqual(
            tuple(pin.capability_id for pin in contract.capabilities),
            ("automation.workflow.create",),
        )
        self.assertFalse(contract.capability("automation.workflow.create").armed)
        self.assertEqual(contract.allowed_tools, ())
        self.assertEqual(
            contract.confirmation_requirements,
            ("automation.workflow.create",),
        )
        self.assertTrue(self.work_db.is_file())
        self.assertFalse(self.chat_db.exists())
        tables = _table_names(self.work_db)
        self.assertIn("tasks", tables)
        self.assertIn("work_contracts", tables)
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
        self.assertNotIn("runtime_frame", kinds)
        self.assertFalse(hasattr(runtime, "frame"))

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

    def test_accept_persists_exactly_one_contract(self) -> None:
        runtime = self._runtime()
        work_id = runtime.accept(_brief(), "execute_external")
        with sqlite3.connect(self.work_db) as db:
            rows = db.execute(
                "SELECT work_id, contract_id, sha256 FROM work_contracts WHERE work_id=?",
                (work_id,),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        contract = runtime.contract(work_id)
        self.assertEqual(rows[0][1], contract.contract_id)
        self.assertEqual(rows[0][2], contract.sha256)

    def test_contract_hash_validates_on_load(self) -> None:
        runtime = self._runtime()
        work_id = runtime.accept(_brief(), "execute_external")
        loaded = runtime.contract(work_id)
        self.assertEqual(loaded.sha256, runtime.contract(work_id).sha256)
        self.assertEqual(
            loaded.as_payload()["work_id"],
            work_id,
        )

    def test_tampered_contract_payload_fails_closed(self) -> None:
        runtime = self._runtime()
        work_id = runtime.accept(_brief(), "execute_external")
        with sqlite3.connect(self.work_db) as db:
            db.execute(
                "UPDATE work_contracts SET payload_json=? WHERE work_id=?",
                ('{"work_id":"tampered"}', work_id),
            )
            db.commit()
        with self.assertRaises(WorkError) as ctx:
            runtime.contract(work_id)
        self.assertIn("digest", str(ctx.exception).casefold())

    def test_get_uses_contract_not_steps_for_capabilities(self) -> None:
        runtime = self._runtime()
        work_id = runtime.accept(_brief(), "execute_external")
        runtime._engine.store.add_step(
            work_id,
            description="Foreign step",
            capability="reasoning.general",
            capability_version="9.9.9",
        )
        self.assertEqual(
            runtime.get(work_id).capabilities,
            ("automation.workflow.create",),
        )
        steps = runtime._engine.store.list_steps(work_id)
        self.assertGreater(len(steps), 1)

    def test_duplicate_contract_insert_is_rejected(self) -> None:
        runtime = self._runtime()
        work_id = runtime.accept(_brief(), "execute_external")
        contract = runtime.contract(work_id)
        with self.assertRaises(TaskStoreError):
            runtime._engine.store.insert_work_contract(
                work_id=contract.work_id,
                contract_id=contract.contract_id + "-dup",
                sha256=contract.sha256,
                payload=contract.as_payload(),
                compiled_at=contract.compiled_at,
            )

    def test_pinned_profile_version_is_used_on_steps(self) -> None:
        profiles = ExecutionProfileIndex()
        profiles.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                version="2.0.0",
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
        pin = runtime.contract(work_id).capability("automation.workflow.create")
        self.assertTrue(pin.armed)
        self.assertEqual(pin.profile_version, "2.0.0")
        steps = runtime._engine.store.list_steps(work_id)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].capability, "automation.workflow.create")
        self.assertEqual(steps[0].capability_version, "2.0.0")

    def test_unarmed_step_does_not_hardcode_version(self) -> None:
        runtime = self._runtime()
        work_id = runtime.accept(_brief(), "execute_external")
        step = runtime._engine.store.list_steps(work_id)[0]
        self.assertIsNone(step.capability_version)
        self.assertFalse(
            runtime.contract(work_id).capability("automation.workflow.create").armed
        )

    def test_inputs_attach_only_to_the_named_capability(self) -> None:
        profiles = ExecutionProfileIndex()
        for capability_id in ("knowledge.index", "communication.email.send"):
            profiles.register(
                CapabilityExecutionProfile(
                    capability_id=capability_id,
                    implementation=CapabilityBinding(
                        capability_id, "internal", "record", "1"
                    ),
                    verifier_id="core.nonempty",
                    executor_kind="deterministic",
                ),
                lambda request: CapabilityOutcome(
                    "pass", output={"ok": True}, receipt={"ok": True}
                ),
            )
        runtime = build_work_runtime(db_path=self.work_db, profiles=profiles)
        work_id = runtime.accept(
            TaskBrief(
                objective="Index then email",
                capabilities=("knowledge.index", "communication.email.send"),
                required_authority="communicate",
                expected_effect="Index and send",
            ),
            "communicate",
            inputs={"knowledge.index": {"title": "notes"}},
        )
        by_capability = {
            step.capability: step
            for step in runtime._engine.store.list_steps(work_id)
        }
        index_step = by_capability["knowledge.index"]
        email_step = by_capability["communication.email.send"]
        self.assertEqual(len(index_step.input_artifact_ids), 1)
        self.assertEqual(email_step.input_artifact_ids, ())
        artifact = runtime._engine.store.get_artifact(index_step.input_artifact_ids[0])
        self.assertEqual(artifact.kind, "knowledge_index_request")
        self.assertEqual(artifact.payload, {"title": "notes"})
        kinds = {
            item.kind for item in runtime._engine.store.list_artifacts(work_id)
        }
        self.assertIn("task_brief", kinds)
        self.assertNotIn("runtime_frame", kinds)
        for artifact in runtime._engine.store.list_artifacts(work_id):
            self.assertNotIn(artifact.id, email_step.input_artifact_ids)

    def test_unknown_input_key_is_rejected_before_insert(self) -> None:
        runtime = self._runtime()
        with self.assertRaises(WorkError) as ctx:
            runtime.accept(
                _brief(),
                "execute_external",
                inputs={"knowledge.index": {"title": "nope"}},
            )
        self.assertIn("accepted brief", str(ctx.exception))
        self.assertEqual(runtime._engine.store.list_tasks(), ())

    def test_kind_match_dependencies_are_deterministic(self) -> None:
        profiles = ExecutionProfileIndex()
        profiles.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.search",
                implementation=CapabilityBinding(
                    "knowledge.search", "internal", "search", "1"
                ),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                output_kind="knowledge_search_results",
            ),
            lambda request: CapabilityOutcome(
                "pass", output={"ok": True}, receipt={"ok": True}
            ),
        )
        profiles.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.answer",
                implementation=CapabilityBinding(
                    "knowledge.answer", "internal", "answer", "1"
                ),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                requires_artifact_kinds=("knowledge_search_results",),
                output_kind="grounded_answer",
            ),
            lambda request: CapabilityOutcome(
                "pass", output={"ok": True}, receipt={"ok": True}
            ),
        )
        runtime = build_work_runtime(db_path=self.work_db, profiles=profiles)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search then answer",
                capabilities=("knowledge.search", "knowledge.answer"),
                required_authority="read",
                expected_effect="A grounded local answer",
            ),
            "read",
        )
        by_capability = {
            step.capability: step
            for step in runtime._engine.store.list_steps(work_id)
        }
        search = by_capability["knowledge.search"]
        answer = by_capability["knowledge.answer"]
        self.assertEqual(search.dependencies, ())
        self.assertEqual(answer.dependencies, (search.id,))
        self.assertEqual(runtime._engine.store.ready_steps(work_id)[0].id, search.id)


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {str(row[0]) for row in rows}
