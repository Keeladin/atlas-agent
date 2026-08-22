from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path

from atlas_api.views.work import build_work_detail
from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import CapabilityOutcome
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    ImplementationResolver,
    UnknownRecordError,
    WorkError,
    build_work_runtime,
)
from atlas_core.work.control import (
    is_archived,
    is_pause_requested,
    is_paused,
    running_executions,
)


def _pass(_request):
    return CapabilityOutcome("pass", output={"ok": True}, receipt={"ok": True})


class WorkControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas-work.db"
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.search",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _pass,
        )
        self.runtime = build_work_runtime(db_path=self.db, profiles=inventory)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _accept(self):
        return self.runtime.accept(
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
            inputs={"knowledge.search": {"query": "notes", "limit": 1}},
        )

    def test_pause_before_start_holds_work(self) -> None:
        work_id = self._accept()
        record = self.runtime.pause(work_id)
        state = self.runtime.store.get_work(work_id)
        self.assertTrue(is_paused(state))
        self.assertEqual(record.status, "planned")
        from atlas_api.views.work import build_work_detail

        detail = build_work_detail(self.runtime, work_id)
        self.assertEqual(detail.phase, "paused")
        self.assertIn("run", detail.actions)
        self.assertNotIn("pause", detail.actions)

    def test_run_clears_pause(self) -> None:
        work_id = self._accept()
        self.runtime.pause(work_id)
        result = self.runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        self.assertFalse(is_paused(self.runtime.store.get_work(work_id)))

    def test_archive_hides_from_default_list(self) -> None:
        work_id = self._accept()
        self.runtime.archive(work_id)
        self.assertEqual(self.runtime.store.list_work(), ())
        hidden = self.runtime.store.list_work(archived=True)
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0].id, work_id)
        self.assertTrue(is_archived(hidden[0]))

    def test_delete_removes_work(self) -> None:
        work_id = self._accept()
        self.runtime.delete(work_id)
        self.assertEqual(self.runtime.store.list_work(archived=None), ())

    def test_cannot_archive_while_running_execution(self) -> None:
        work_id = self._accept()
        steps = self.runtime.store.list_steps(work_id)
        self.runtime.store.begin_execution(
            work_id,
            step_id=steps[0].id,
            capability="knowledge.search",
            capability_version="1.0.0",
        )
        with self.assertRaises(WorkError):
            self.runtime.archive(work_id)
        with self.assertRaises(WorkError):
            self.runtime.delete(work_id)

    def test_pause_during_running_step_does_not_start_next(self) -> None:
        started = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        runtime = self._two_step_runtime(
            first=lambda _request: (
                started.set(),
                release.wait(timeout=5),
                _search_outcome(),
            )[-1],
            second=lambda _request: (second_started.set(), _pass(_request))[-1],
        )
        work_id = self._accept_two_step(runtime)
        worker = threading.Thread(target=runtime.run, args=(work_id,), daemon=True)
        worker.start()
        self.assertTrue(started.wait(timeout=5))
        runtime.pause(work_id)
        self.assertTrue(is_pause_requested(runtime.store.get_work(work_id)))
        release.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(second_started.is_set())
        steps = {step.capability: step for step in runtime.store.list_steps(work_id)}
        self.assertEqual(steps["knowledge.search"].status, "pass")
        self.assertEqual(steps["knowledge.answer"].status, "pending")
        self.assertTrue(is_paused(runtime.store.get_work(work_id)))
        self.assertFalse(is_pause_requested(runtime.store.get_work(work_id)))
        self.assertEqual(running_executions(runtime.store.list_executions(work_id)), ())

    def test_pause_between_steps_does_not_begin_next(self) -> None:
        runtime = self._two_step_runtime()
        work_id = self._accept_two_step(runtime)
        contract = runtime.contract(work_id)
        report = ImplementationResolver().resolve(
            contract, runtime._profiles, runtime._tool_gateway
        )
        progressed = runtime._engine.run_once(
            contract, report, max_new_executions=1
        )
        self.assertTrue(progressed)
        steps = {step.capability: step for step in runtime.store.list_steps(work_id)}
        self.assertEqual(steps["knowledge.search"].status, "pass")
        self.assertEqual(steps["knowledge.answer"].status, "pending")
        runtime.pause(work_id)
        result = runtime._engine.run(contract, report)
        self.assertEqual(result.reason, "paused at a safe boundary")
        steps = {step.capability: step for step in runtime.store.list_steps(work_id)}
        self.assertEqual(steps["knowledge.search"].status, "pass")
        self.assertEqual(steps["knowledge.answer"].status, "pending")
        self.assertEqual(
            [item.status for item in runtime.store.list_executions(work_id)],
            ["pass"],
        )

    def test_repeated_pause_is_idempotent(self) -> None:
        work_id = self._accept()
        first = self.runtime.pause(work_id)
        events_after_first = [
            item.name
            for item in self.runtime.store.list_events(work_id)
            if item.name in {"work.paused", "work.pause_requested"}
        ]
        second = self.runtime.pause(work_id)
        third = self.runtime.pause(work_id)
        events_after = [
            item.name
            for item in self.runtime.store.list_events(work_id)
            if item.name in {"work.paused", "work.pause_requested"}
        ]
        self.assertEqual(first.status, second.status)
        self.assertEqual(second.status, third.status)
        self.assertTrue(is_paused(self.runtime.store.get_work(work_id)))
        self.assertFalse(is_pause_requested(self.runtime.store.get_work(work_id)))
        self.assertEqual(events_after, events_after_first)
        self.assertEqual(
            self.runtime.store.get_work(work_id).metadata["source"],
            "task_brief",
        )

    def test_resume_clears_only_pause_flags(self) -> None:
        work_id = self._accept()
        self.runtime.store.merge_work_metadata(
            work_id, {"owner": "keep-me", "control": {"note": "keep"}}
        )
        self.runtime.pause(work_id)
        self.assertIn("brief", self.runtime.store.get_work(work_id).metadata)
        result = self.runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        state = self.runtime.store.get_work(work_id)
        self.assertFalse(is_paused(state))
        self.assertFalse(is_pause_requested(state))
        self.assertEqual(state.metadata["owner"], "keep-me")
        self.assertEqual(state.metadata["control"]["note"], "keep")
        self.assertIn("brief", state.metadata)
        by_step = Counter(
            item.step_id
            for item in self.runtime.store.list_executions(work_id)
            if item.status == "pass"
        )
        self.assertEqual(list(by_step.values()), [1])

    def test_concurrent_run_cannot_execute_a_step_twice(self) -> None:
        runtime = self._two_step_runtime()
        work_id = self._accept_two_step(runtime)
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait(timeout=5)
                runtime.run(work_id)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, daemon=True),
            threading.Thread(target=worker, daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        by_step = Counter(
            item.step_id
            for item in runtime.store.list_executions(work_id)
            if item.status in {"pass", "running", "fail"}
        )
        for step in runtime.store.list_steps(work_id):
            self.assertLessEqual(by_step[step.id], 1, step.capability)
        self.assertEqual(runtime.get(work_id).status, "completed")

    def test_cancel_while_paused_is_deterministic(self) -> None:
        work_id = self._accept()
        self.runtime.pause(work_id)
        cancelled = self.runtime.store.set_work_status(
            work_id, "cancelled", force=True
        )
        self.assertEqual(cancelled.status, "cancelled")
        detail = build_work_detail(self.runtime, work_id)
        self.assertEqual(detail.phase, "terminal")
        self.assertEqual(detail.status, "cancelled")
        self.assertNotIn("run", detail.actions)
        self.assertEqual(self.runtime.store.list_executions(work_id), ())
        again = self.runtime.store.set_work_status(work_id, "cancelled", force=True)
        self.assertEqual(again.status, "cancelled")

    def test_archive_and_delete_lose_to_running_execution(self) -> None:
        started = threading.Event()
        release = threading.Event()
        runtime = self._gated_runtime(started, release)
        work_id = self._accept_on(runtime)
        worker = threading.Thread(target=runtime.run, args=(work_id,), daemon=True)
        worker.start()
        self.assertTrue(started.wait(timeout=5))
        with self.assertRaises(WorkError):
            runtime.archive(work_id)
        with self.assertRaises(WorkError):
            runtime.delete(work_id)
        self.assertFalse(is_archived(runtime.store.get_work(work_id)))
        release.set()
        worker.join(timeout=5)
        self.assertEqual(runtime.get(work_id).status, "completed")

    def test_delete_after_stop_cascades(self) -> None:
        work_id = self._accept()
        self.runtime.run(work_id)
        self.runtime.delete(work_id)
        with self.assertRaises(UnknownRecordError):
            self.runtime.store.get_work(work_id)
        leftover = _owned_row_counts(self.db, work_id)
        self.assertEqual(leftover, {})

    def test_archived_work_remains_on_direct_lookup(self) -> None:
        work_id = self._accept()
        self.runtime.archive(work_id)
        self.assertEqual(self.runtime.store.list_work(), ())
        record = self.runtime.get(work_id)
        self.assertEqual(record.id, work_id)
        detail = build_work_detail(self.runtime, work_id)
        self.assertEqual(detail.phase, "archived")
        self.assertEqual(detail.work_id, work_id)

    def test_restore_does_not_alter_execution_state(self) -> None:
        work_id = self._accept()
        result = self.runtime.run(work_id)
        executions = self.runtime.store.list_executions(work_id)
        steps = tuple(
            (step.id, step.status) for step in self.runtime.store.list_steps(work_id)
        )
        status = self.runtime.get(work_id).status
        self.runtime.archive(work_id)
        restored = self.runtime.archive(work_id, archived=False)
        self.assertEqual(restored.status, status)
        self.assertEqual(result.status, status)
        self.assertEqual(
            tuple(
                (step.id, step.status)
                for step in self.runtime.store.list_steps(work_id)
            ),
            steps,
        )
        self.assertEqual(
            [item.id for item in self.runtime.store.list_executions(work_id)],
            [item.id for item in executions],
        )
        self.assertFalse(is_archived(self.runtime.store.get_work(work_id)))

    def test_control_merge_preserves_unrelated_metadata(self) -> None:
        work_id = self._accept()
        barrier = threading.Barrier(2)

        def pause() -> None:
            barrier.wait(timeout=5)
            self.runtime.pause(work_id)

        def note() -> None:
            barrier.wait(timeout=5)
            self.runtime.store.merge_work_metadata(
                work_id, {"control": {"note": "keep"}, "owner": "atlas"}
            )

        threads = [
            threading.Thread(target=pause, daemon=True),
            threading.Thread(target=note, daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        state = self.runtime.store.get_work(work_id)
        self.assertTrue(is_paused(state))
        self.assertEqual(state.metadata["owner"], "atlas")
        self.assertEqual(state.metadata["control"]["note"], "keep")
        self.assertIn("brief", state.metadata)
        self.assertEqual(state.metadata["source"], "task_brief")

    def _two_step_runtime(self, first=None, second=None):
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.search",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                output_kind="knowledge_search_results",
                parallel_safe=False,
            ),
            first or (lambda _request: _search_outcome()),
        )
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.answer",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                requires_artifact_kinds=("knowledge_search_results",),
                parallel_safe=False,
            ),
            second or _pass,
        )
        return build_work_runtime(db_path=self.db, profiles=inventory)

    def _gated_runtime(self, started: threading.Event, release: threading.Event):
        inventory = DeploymentInventory()

        def gated(_request):
            started.set()
            release.wait(timeout=5)
            return _pass(_request)

        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.search",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            gated,
        )
        return build_work_runtime(db_path=self.db, profiles=inventory)

    def _accept_on(self, runtime):
        return runtime.accept(
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
            inputs={"knowledge.search": {"query": "notes", "limit": 1}},
        )

    def _accept_two_step(self, runtime):
        return runtime.accept(
            TaskBrief(
                objective="Search then answer from local knowledge",
                capabilities=("knowledge.search", "knowledge.answer"),
                required_authority="modify_internal",
                expected_effect="A grounded local answer",
            ),
            "modify_internal",
            inputs={"knowledge.search": {"query": "notes", "limit": 1}},
        )


def _search_outcome() -> CapabilityOutcome:
    return CapabilityOutcome(
        "pass",
        output={"query": "notes", "results": [], "status": "no_relevant_results"},
        output_kind="knowledge_search_results",
        receipt={"ok": True},
    )


def _owned_row_counts(path: Path, work_id: str) -> dict[str, int]:
    tables = (
        "work",
        "work_criteria",
        "work_steps",
        "work_artifacts",
        "work_executions",
        "work_context_manifests",
        "work_checkpoints",
        "work_claims",
        "work_approvals",
        "work_confirmations",
        "work_events",
        "work_contracts",
    )
    leftover: dict[str, int] = {}
    with sqlite3.connect(path) as db:
        for table in tables:
            column = "id" if table == "work" else "work_id"
            row = db.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {column}=?",
                (work_id,),
            ).fetchone()
            if int(row[0]):
                leftover[table] = int(row[0])
    return leftover
