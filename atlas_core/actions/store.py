from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import ActionOccurrence, ActionRequest, payload_sha256


class ActionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self, db: sqlite3.Connection | None = None):
        if db is not None:
            yield db
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path); conn.row_factory = sqlite3.Row; conn.execute("PRAGMA busy_timeout=5000")
        try:
            with conn: yield conn
        finally: conn.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS action_occurrences (
                occurrence_id TEXT PRIMARY KEY, capability_id TEXT NOT NULL, operation TEXT NOT NULL, scope TEXT NOT NULL,
                payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL, principal_id TEXT NOT NULL, principal_kind TEXT NOT NULL,
                surface TEXT NOT NULL, policy_decision TEXT NOT NULL CHECK(policy_decision IN ('NO','YES','CONFIRM')),
                policy_revision INTEGER NOT NULL, policy_event_id TEXT,
                status TEXT NOT NULL CHECK(status IN ('blocked','pending_confirmation','executing','succeeded','failed','uncertain','expired','cancelled')),
                work_id TEXT, step_id TEXT, summary TEXT, result_json TEXT, receipt_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT, error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, confirmed_at TEXT, executed_at TEXT, completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS occurrence_status ON action_occurrences(status,created_at);
            CREATE INDEX IF NOT EXISTS occurrence_work ON action_occurrences(work_id,step_id,created_at);
            """)

    def create(self, request: ActionRequest, *, decision: str, revision: int, event_id: str | None, status: str) -> ActionOccurrence:
        oid = f"action_{uuid4().hex}"
        encoded = json.dumps(request.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        digest = payload_sha256(request.payload)
        with self._db() as db:
            db.execute("""INSERT INTO action_occurrences(occurrence_id,capability_id,operation,scope,payload_json,payload_sha256,principal_id,principal_kind,surface,policy_decision,policy_revision,policy_event_id,status,work_id,step_id,summary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (oid, request.capability_id, request.operation, request.scope, encoded, digest, request.provenance.principal_id, request.provenance.principal_kind, request.provenance.surface, decision, revision, event_id, status, request.work_id, request.step_id, request.summary))
        return self.get(oid)

    def get(self, occurrence_id: str) -> ActionOccurrence:
        with self._db() as db:
            row = db.execute("SELECT * FROM action_occurrences WHERE occurrence_id=?", (occurrence_id,)).fetchone()
        if row is None: raise KeyError(f"unknown action occurrence: {occurrence_id}")
        return _occurrence(row)

    def pending(self, *, principal_id: str | None = None) -> tuple[ActionOccurrence, ...]:
        sql = "SELECT * FROM action_occurrences WHERE status='pending_confirmation'"; args: list[Any] = []
        if principal_id:
            sql += " AND principal_id=?"; args.append(principal_id)
        sql += " ORDER BY created_at"
        with self._db() as db: rows = db.execute(sql, args).fetchall()
        return tuple(_occurrence(row) for row in rows)

    def recent(self, *, limit: int = 100, work_id: str | None = None) -> tuple[ActionOccurrence, ...]:
        if work_id:
            sql = "SELECT * FROM action_occurrences WHERE work_id=? ORDER BY created_at DESC LIMIT ?"; args=(work_id, limit)
        else:
            sql = "SELECT * FROM action_occurrences ORDER BY created_at DESC LIMIT ?"; args=(limit,)
        with self._db() as db: rows = db.execute(sql, args).fetchall()
        return tuple(_occurrence(row) for row in rows)

    def transition(self, occurrence_id: str, *, from_status: tuple[str, ...], to_status: str, **fields: Any) -> ActionOccurrence:
        allowed = {"policy_decision","policy_revision","policy_event_id","result_json","receipt_json","error_code","error","confirmed_at","executed_at","completed_at"}
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

    def redact_memory_content(self, db: sqlite3.Connection, *, principal_id: str, item_ids: set[str], content_hashes: set[str]) -> list[dict[str, Any]]:
        """Strip target memory content from terminal memory occurrences inside a caller-owned transaction."""
        from atlas_core.memory.store import memory_content_hash

        content_keys = {"content", "title", "grounding_excerpt", "text"}

        def strings(value: Any):
            if isinstance(value, dict):
                for child in value.values():
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)
            elif isinstance(value, str):
                yield value

        def content_values(value: Any, key: str | None = None):
            if isinstance(value, dict):
                for child_key, child in value.items():
                    yield from content_values(child, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    yield from content_values(child, key)
            elif isinstance(value, str) and key in content_keys:
                yield value

        def direct_item_match(value: dict[str, Any]) -> bool:
            for child in value.values():
                if isinstance(child, str) and any(item_id in child for item_id in item_ids):
                    return True
            return False

        def redact(value: Any, path: str = "", *, mark_root: bool = False) -> tuple[Any, list[str]]:
            fields: list[str] = []
            if isinstance(value, dict):
                target_dict = direct_item_match(value)
                out: dict[str, Any] = {}
                for key, child in value.items():
                    child_path = f"{path}.{key}" if path else str(key)
                    if str(key) in content_keys and isinstance(child, str) and (target_dict or memory_content_hash(child) in content_hashes):
                        out[key] = "[purged]"
                        fields.append(child_path)
                    else:
                        out[key], nested = redact(child, child_path)
                        fields.extend(nested)
                if fields or mark_root:
                    out["__redacted"] = True
                return out, fields
            if isinstance(value, list):
                rows = []
                for index, child in enumerate(value):
                    redacted, nested = redact(child, f"{path}[{index}]")
                    rows.append(redacted)
                    fields.extend(nested)
                return rows, fields
            return value, fields

        rows = db.execute(
            """SELECT occurrence_id,payload_json,result_json,receipt_json,summary,status
            FROM action_occurrences
            WHERE principal_id=? AND (scope='atlas/memory' OR scope LIKE 'atlas/memory/%')
              AND status NOT IN ('pending_confirmation','executing')
            ORDER BY created_at,occurrence_id""",
            (principal_id,),
        ).fetchall()
        redacted_rows: list[dict[str, Any]] = []
        for row in rows:
            decoded: dict[str, Any] = {}
            for column in ("payload_json", "result_json", "receipt_json"):
                raw = row[column]
                try:
                    decoded[column] = None if raw is None else json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    decoded[column] = raw
            item_match = any(
                any(item_id in text for item_id in item_ids)
                for value in decoded.values() for text in strings(value)
            )
            hash_match = any(
                memory_content_hash(text) in content_hashes
                for value in decoded.values() for text in content_values(value)
            )
            summary = row["summary"]
            if isinstance(summary, str):
                item_match = item_match or any(item_id in summary for item_id in item_ids)
                hash_match = hash_match or memory_content_hash(summary) in content_hashes
            if not (item_match or hash_match):
                continue

            fields: list[str] = []
            encoded: dict[str, str | None] = {}
            for column, value in decoded.items():
                if value is None:
                    encoded[column] = None
                    continue
                redacted, nested = redact(value, column, mark_root=(column == "payload_json" and isinstance(value, dict)))
                fields.extend(nested)
                encoded[column] = json.dumps(redacted, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            fields.append("summary")
            changed = db.execute(
                """UPDATE action_occurrences SET payload_json=?,result_json=?,receipt_json=?,summary='[purged]'
                WHERE occurrence_id=? AND status NOT IN ('pending_confirmation','executing')""",
                (encoded["payload_json"], encoded["result_json"], encoded["receipt_json"], row["occurrence_id"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("memory redaction lost its terminal-status guard")
            matched = []
            if item_match:
                matched.append("item_id")
            if hash_match:
                matched.append("content_hash")
            redacted_rows.append({"occurrence_id": row["occurrence_id"], "matched_by": matched, "fields": sorted(set(fields))})
        return redacted_rows


def _load(value: str | None, default: Any) -> Any:
    return default if not value else json.loads(value)

def _occurrence(row: sqlite3.Row) -> ActionOccurrence:
    return ActionOccurrence(
        occurrence_id=row["occurrence_id"], capability_id=row["capability_id"], operation=row["operation"], scope=row["scope"],
        payload=_load(row["payload_json"], {}), payload_sha256=row["payload_sha256"], principal_id=row["principal_id"], principal_kind=row["principal_kind"], surface=row["surface"],
        policy_decision=row["policy_decision"], policy_revision=int(row["policy_revision"]), policy_event_id=row["policy_event_id"], status=row["status"], work_id=row["work_id"], step_id=row["step_id"], summary=row["summary"],
        result=_load(row["result_json"], None), receipt=_load(row["receipt_json"], {}), error_code=row["error_code"], error=row["error"], created_at=row["created_at"], confirmed_at=row["confirmed_at"], executed_at=row["executed_at"], completed_at=row["completed_at"],
    )
