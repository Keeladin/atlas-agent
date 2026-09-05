from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


class OperationalStateStore:
    """Durable operational truth for quarantine, repair, and clearance."""

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
            db.executescript("""
            CREATE TABLE IF NOT EXISTS runtime_control(
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1)),
                active_event_id TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT OR IGNORE INTO runtime_control(singleton_id,quarantined)
            VALUES (1,0);
            CREATE TABLE IF NOT EXISTS operational_events(
                event_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('quarantine_entered','repair','quarantine_cleared')),
                runtime_revision TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS operational_events_created
                ON operational_events(created_at,event_id);
            """)

    def state(self) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM runtime_control WHERE singleton_id=1").fetchone()
        return dict(row)
    def enter_quarantine(
        self, violations: Iterable[Any], *, runtime_revision: str,
        actor: str = "startup_validation",
    ) -> str:
        evidence = [
            {
                "code": str(getattr(item, "code", "unknown")),
                "reference": str(getattr(item, "reference", "")),
                "detail": str(getattr(item, "detail", "")),
            }
            for item in violations
        ]
        with self._db() as db:
            current = db.execute(
                "SELECT quarantined,active_event_id FROM runtime_control WHERE singleton_id=1"
            ).fetchone()
            if current is not None and int(current["quarantined"]) == 1:
                return str(current["active_event_id"] or "")
            event_id = f"operational_{uuid4().hex}"
            db.execute(
                """INSERT INTO operational_events(
                       event_id,kind,runtime_revision,actor,reason,evidence_json
                   ) VALUES (?,'quarantine_entered',?,?,?,?)""",
                (event_id, runtime_revision, actor, "runtime invariant violation",
                 json.dumps({"violations": evidence}, sort_keys=True, separators=(",", ":"))),
            )
            db.execute(
                """UPDATE runtime_control
                   SET quarantined=1,active_event_id=?,updated_at=CURRENT_TIMESTAMP
                   WHERE singleton_id=1""",
                (event_id,),
            )
        return event_id

    def record_repair(
        self, *, runtime_revision: str, actor: str, reason: str,
        evidence: dict[str, Any],
    ) -> str:
        event_id = f"operational_{uuid4().hex}"
        with self._db() as db:
            state = db.execute(
                "SELECT quarantined FROM runtime_control WHERE singleton_id=1"
            ).fetchone()
            if state is None or int(state["quarantined"]) != 1:
                raise ValueError("repair events are only valid while quarantined")
            db.execute(
                """INSERT INTO operational_events(
                       event_id,kind,runtime_revision,actor,reason,evidence_json
                   ) VALUES (?,'repair',?,?,?,?)""",
                (event_id, runtime_revision, actor, reason,
                 json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)),
            )
        return event_id
    def clear_quarantine(
        self, *, runtime_revision: str, actor: str,
        validation_evidence: dict[str, Any],
    ) -> str:
        event_id = f"operational_{uuid4().hex}"
        with self._db() as db:
            state = db.execute(
                "SELECT quarantined,active_event_id FROM runtime_control WHERE singleton_id=1"
            ).fetchone()
            if state is None or int(state["quarantined"]) != 1:
                raise ValueError("runtime is not quarantined")
            db.execute(
                """INSERT INTO operational_events(
                       event_id,kind,runtime_revision,actor,reason,evidence_json
                   ) VALUES (?,'quarantine_cleared',?,?,?,?)""",
                (event_id, runtime_revision, actor, "explicit invariant clearance",
                 json.dumps(validation_evidence, sort_keys=True, separators=(",", ":"), default=str)),
            )
            db.execute(
                """UPDATE runtime_control
                   SET quarantined=0,active_event_id=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE singleton_id=1"""
            )
        return event_id

    def events(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM operational_events ORDER BY created_at DESC,event_id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
            result.append(item)
        return tuple(result)
