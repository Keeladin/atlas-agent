from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_core.database import WorkDatabase, as_work_database


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    occurrence_id: str
    kind: str
    payload: dict[str, Any]
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, "occurrence_id": self.occurrence_id, "kind": self.kind, "payload": self.payload, "created_at": self.created_at}


class EvidenceStore:
    def __init__(self, database: WorkDatabase | str | Path) -> None:
        self.database = as_work_database(database)
        self.path = self.database.path

    @contextmanager
    def _db(self):
        with self.database.connection() as db:
            yield db

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL, kind TEXT NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS evidence_occurrence ON evidence(occurrence_id,created_at);
            """)

    def add(self, occurrence_id: str, kind: str, payload: dict[str, Any]) -> EvidenceRecord:
        evidence_id = f"evidence_{uuid4().hex}"
        with self._db() as db:
            db.execute("INSERT INTO evidence(evidence_id,occurrence_id,kind,payload_json) VALUES (?,?,?,?)", (evidence_id, occurrence_id, kind, json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)))
            row = db.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        return _record(row)

    def for_occurrence(self, occurrence_id: str) -> tuple[EvidenceRecord, ...]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM evidence WHERE occurrence_id=? ORDER BY created_at,evidence_id", (occurrence_id,)).fetchall()
        return tuple(_record(row) for row in rows)


def _record(row: sqlite3.Row) -> EvidenceRecord:
    return EvidenceRecord(row["evidence_id"], row["occurrence_id"], row["kind"], json.loads(row["payload_json"]), row["created_at"])
