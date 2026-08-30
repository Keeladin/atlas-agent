from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .models import PolicyDecision, PolicyRule, VALID_DECISIONS, normalize_operation, normalize_scope


class PolicyStore:
    """Append-only owner-policy history. No row means NO."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS policy_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    principal_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('NO','YES','CONFIRM')),
                    reason TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS policy_events_lookup
                    ON policy_events(principal_id, scope, operation, sequence);
                CREATE TRIGGER IF NOT EXISTS policy_events_no_update
                    BEFORE UPDATE ON policy_events BEGIN SELECT RAISE(ABORT, 'policy_events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS policy_events_no_delete
                    BEFORE DELETE ON policy_events BEGIN SELECT RAISE(ABORT, 'policy_events are append-only'); END;
                """
            )

    def set(
        self, *, principal_id: str, scope: str, operation: str, decision: PolicyDecision,
        reason: str | None = None,
    ) -> PolicyRule:
        principal = str(principal_id or "").strip()
        if not principal:
            raise ValueError("principal_id must not be empty")
        resolved_scope = normalize_scope(scope)
        resolved_operation = normalize_operation(operation)
        if decision not in VALID_DECISIONS:
            raise ValueError(f"unsupported policy decision: {decision}")
        event_id = f"policy_{uuid4().hex}"
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO policy_events(event_id,principal_id,scope,operation,decision,reason) VALUES (?,?,?,?,?,?)",
                (event_id, principal, resolved_scope, resolved_operation, decision, reason),
            )
            sequence = int(cur.lastrowid)
            row = db.execute("SELECT * FROM policy_events WHERE sequence=?", (sequence,)).fetchone()
        return _rule(row)

    def snapshot(self, principal_id: str) -> tuple[tuple[PolicyRule, ...], int]:
        with self._db() as db:
            revision = int(db.execute("SELECT COALESCE(MAX(sequence),0) AS revision FROM policy_events").fetchone()["revision"])
            rows = db.execute(
                """
                SELECT p.* FROM policy_events p
                JOIN (
                    SELECT principal_id,scope,operation,MAX(sequence) AS sequence
                    FROM policy_events WHERE principal_id=?
                    GROUP BY principal_id,scope,operation
                ) latest ON latest.sequence=p.sequence
                ORDER BY p.scope,p.operation
                """,
                (principal_id,),
            ).fetchall()
        return tuple(_rule(row) for row in rows), revision

    def latest_rules(self, principal_id: str) -> tuple[PolicyRule, ...]:
        with self._db() as db:
            rows = db.execute(
                """
                SELECT p.* FROM policy_events p
                JOIN (
                    SELECT principal_id,scope,operation,MAX(sequence) AS sequence
                    FROM policy_events WHERE principal_id=?
                    GROUP BY principal_id,scope,operation
                ) latest ON latest.sequence=p.sequence
                ORDER BY p.scope,p.operation
                """,
                (principal_id,),
            ).fetchall()
        return tuple(_rule(row) for row in rows)

    def history(self, principal_id: str, *, limit: int = 200) -> tuple[PolicyRule, ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM policy_events WHERE principal_id=? ORDER BY sequence DESC LIMIT ?",
                (principal_id, max(1, min(int(limit), 1000))),
            ).fetchall()
        return tuple(_rule(row) for row in rows)

    def revision(self) -> int:
        with self._db() as db:
            row = db.execute("SELECT COALESCE(MAX(sequence),0) AS revision FROM policy_events").fetchone()
        return int(row["revision"])

    def seed_if_absent(
        self, *, principal_id: str, scope: str, operation: str, decision: PolicyDecision,
        reason: str = "visible initial policy",
    ) -> PolicyRule | None:
        resolved_scope = normalize_scope(scope)
        resolved_operation = normalize_operation(operation)
        with self._db() as db:
            row = db.execute(
                "SELECT 1 FROM policy_events WHERE principal_id=? AND scope=? AND operation=? LIMIT 1",
                (principal_id, resolved_scope, resolved_operation),
            ).fetchone()
        if row is not None:
            return None
        return self.set(
            principal_id=principal_id, scope=resolved_scope, operation=resolved_operation,
            decision=decision, reason=reason,
        )


def _rule(row: sqlite3.Row) -> PolicyRule:
    return PolicyRule(
        event_id=row["event_id"], sequence=int(row["sequence"]), principal_id=row["principal_id"],
        scope=row["scope"], operation=row["operation"], decision=row["decision"],
        reason=row["reason"], created_at=row["created_at"],
    )
