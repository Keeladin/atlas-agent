from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_core.database import WorkDatabase, as_work_database

from .models import ActionOccurrence, ActionRequest, payload_sha256


class ActionStore:
    def __init__(self, database: WorkDatabase | str | Path) -> None:
        self.database = as_work_database(database)
        self.path = self.database.path

    @contextmanager
    def _db(self, db: sqlite3.Connection | None = None):
        with self.database.connection(db) as conn:
            yield conn

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS action_occurrences (
                occurrence_id TEXT PRIMARY KEY, capability_id TEXT NOT NULL, operation TEXT NOT NULL, scope TEXT NOT NULL,
                payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL, principal_id TEXT NOT NULL, principal_kind TEXT NOT NULL,
                surface TEXT NOT NULL, policy_decision TEXT NOT NULL CHECK(policy_decision IN ('NO','YES')),
                policy_revision INTEGER NOT NULL, policy_event_id TEXT,
                status TEXT NOT NULL CHECK(status IN ('blocked','executing','succeeded','failed','uncertain')),
                work_id TEXT, step_id TEXT, summary TEXT, result_json TEXT, receipt_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT, error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, executed_at TEXT, completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS occurrence_status ON action_occurrences(status,created_at);
            CREATE INDEX IF NOT EXISTS occurrence_work ON action_occurrences(work_id,step_id,created_at);
            """)

    def create(self, request: ActionRequest, *, decision: str, revision: int, event_id: str | None, status: str) -> ActionOccurrence:
        oid = f"action_{uuid4().hex}"
        encoded = json.dumps(request.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        digest = payload_sha256(request.payload)
        initial_receipt = json.dumps(request.initial_receipt or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        with self._db() as db:
            db.execute("""INSERT INTO action_occurrences(occurrence_id,capability_id,operation,scope,payload_json,payload_sha256,principal_id,principal_kind,surface,policy_decision,policy_revision,policy_event_id,status,work_id,step_id,summary,receipt_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (oid, request.capability_id, request.operation, request.scope, encoded, digest, request.provenance.principal_id, request.provenance.principal_kind, request.provenance.surface, decision, revision, event_id, status, request.work_id, request.step_id, request.summary, initial_receipt))
        return self.get(oid)

    def get(self, occurrence_id: str) -> ActionOccurrence:
        with self._db() as db:
            row = db.execute("SELECT * FROM action_occurrences WHERE occurrence_id=?", (occurrence_id,)).fetchone()
        if row is None: raise KeyError(f"unknown action occurrence: {occurrence_id}")
        return _occurrence(row)

    def recent(self, *, limit: int = 100, work_id: str | None = None) -> tuple[ActionOccurrence, ...]:
        if work_id:
            sql = "SELECT * FROM action_occurrences WHERE work_id=? ORDER BY created_at DESC LIMIT ?"; args=(work_id, limit)
        else:
            sql = "SELECT * FROM action_occurrences ORDER BY created_at DESC LIMIT ?"; args=(limit,)
        with self._db() as db: rows = db.execute(sql, args).fetchall()
        return tuple(_occurrence(row) for row in rows)


    def for_work_step(self, work_id: str, step_id: str) -> tuple[ActionOccurrence, ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM action_occurrences WHERE work_id=? AND step_id=? ORDER BY created_at,occurrence_id",
                (work_id, step_id),
            ).fetchall()
        return tuple(_occurrence(row) for row in rows)

    def attach_to_work(self, occurrence_id: str, *, work_id: str, step_id: str) -> ActionOccurrence:
        with self._db() as db:
            changed = db.execute(
                """UPDATE action_occurrences SET work_id=?,step_id=?
                   WHERE occurrence_id=? AND status='uncertain' AND work_id IS NULL AND step_id IS NULL""",
                (work_id, step_id, occurrence_id),
            ).rowcount
        if changed != 1:
            raise ValueError("action occurrence cannot be adopted into Work")
        return self.get(occurrence_id)

    def unresolved(self, *, capability_id: str | None = None, scope: str | None = None) -> tuple[ActionOccurrence, ...]:
        clauses = ["status IN ('executing','uncertain')"]; args: list[Any] = []
        if capability_id is not None:
            clauses.append("capability_id=?"); args.append(capability_id)
        if scope is not None:
            clauses.append("scope=?"); args.append(scope)
        sql = "SELECT * FROM action_occurrences WHERE " + " AND ".join(clauses) + " ORDER BY created_at,occurrence_id"
        with self._db() as db:
            rows = db.execute(sql, args).fetchall()
        return tuple(_occurrence(row) for row in rows)

    def transition(self, occurrence_id: str, *, from_status: tuple[str, ...], to_status: str, **fields: Any) -> ActionOccurrence:
        allowed = {"policy_decision","policy_revision","policy_event_id","result_json","receipt_json","error_code","error","executed_at","completed_at"}
        bad=set(fields)-allowed
        if bad: raise ValueError(f"unsupported occurrence fields: {sorted(bad)}")
        assignments=["status=?"]; values: list[Any]=[to_status]
        for key,value in fields.items(): assignments.append(f"{key}=?"); values.append(value)
        values.extend([occurrence_id, *from_status])
        placeholders=",".join("?" for _ in from_status)
        with self._db() as db:
            changed=db.execute(f"UPDATE action_occurrences SET {','.join(assignments)} WHERE occurrence_id=? AND status IN ({placeholders})", values).rowcount
        if changed != 1: raise ValueError("action occurrence state changed or is not eligible")
        return self.get(occurrence_id)


    def recover_executing(self) -> int:
        """On process start, preserve pre-dispatch evidence and mark abandoned actions uncertain.

        Atlas cannot know whether an external side effect happened after the old
        process disappeared, so restart recovery must never silently retry it.
        """
        changed = 0
        with self._db() as db:
            rows = db.execute("SELECT occurrence_id,receipt_json FROM action_occurrences WHERE status='executing'").fetchall()
            for row in rows:
                receipt = _load(row["receipt_json"], {})
                if not isinstance(receipt, dict): receipt = {}
                receipt = {
                    **receipt,
                    "recovery_required": True,
                    "recovery_reason": "runtime restarted while action was executing",
                }
                changed += db.execute(
                    """UPDATE action_occurrences
                       SET status='uncertain',receipt_json=?,error_code='runtime_restart_uncertain',
                           error='runtime restarted while action outcome was unresolved'
                       WHERE occurrence_id=? AND status='executing'""",
                    (json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str), row["occurrence_id"]),
                ).rowcount
        return int(changed)

    def memory_occurrence_rows(self, db: sqlite3.Connection, *, principal_id: str) -> tuple[dict[str, Any], ...]:
        rows = db.execute(
            """SELECT occurrence_id,capability_id,payload_json,result_json,receipt_json,summary,status
            FROM action_occurrences
            WHERE principal_id=? AND (scope='atlas/memory' OR scope LIKE 'atlas/memory/%')
            ORDER BY created_at,occurrence_id""",
            (principal_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def redact_memory_occurrence(self, db: sqlite3.Connection, *, occurrence_id: str,
                                 payload_json: str, result_json: str | None, receipt_json: str,
                                 summary: str = "[purged]") -> None:
        changed = db.execute(
            """UPDATE action_occurrences
            SET payload_json=?,result_json=?,receipt_json=?,summary=?
            WHERE occurrence_id=? AND status!='executing'""",
            (payload_json, result_json, receipt_json, summary, occurrence_id),
        ).rowcount
        if changed != 1:
            raise RuntimeError("memory redaction lost its terminal-status guard")


def _load(value: str | None, default: Any) -> Any:
    return default if not value else json.loads(value)

def _occurrence(row: sqlite3.Row) -> ActionOccurrence:
    return ActionOccurrence(
        occurrence_id=row["occurrence_id"], capability_id=row["capability_id"], operation=row["operation"], scope=row["scope"],
        payload=_load(row["payload_json"], {}), payload_sha256=row["payload_sha256"], principal_id=row["principal_id"], principal_kind=row["principal_kind"], surface=row["surface"],
        policy_decision=row["policy_decision"], policy_revision=int(row["policy_revision"]), policy_event_id=row["policy_event_id"], status=row["status"], work_id=row["work_id"], step_id=row["step_id"], summary=row["summary"],
        result=_load(row["result_json"], None), receipt=_load(row["receipt_json"], {}), error_code=row["error_code"], error=row["error"], created_at=row["created_at"], executed_at=row["executed_at"], completed_at=row["completed_at"],
    )
