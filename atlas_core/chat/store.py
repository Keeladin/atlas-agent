from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


class ChatStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
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
            CREATE TABLE IF NOT EXISTS conversations(
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS chat_turns(
                turn_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant','tool','system')),
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS chat_turns_conversation
                ON chat_turns(conversation_id,created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS chat_work_completion_unique
                ON chat_turns(json_extract(metadata_json,'$.work_completion_key'))
                WHERE json_extract(metadata_json,'$.work_completion_key') IS NOT NULL;
            """)

    def create_conversation(self, title: str = "New conversation") -> dict[str, Any]:
        cid = f"conversation_{uuid4().hex}"
        with self._db() as db:
            db.execute("INSERT INTO conversations(conversation_id,title) VALUES (?,?)", (cid, title))
        return self.conversation(cid)

    def conversation(self, cid: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM conversations WHERE conversation_id=?", (cid,)).fetchone()
        if row is None:
            raise KeyError(cid)
        return dict(row)

    def delete_conversation(self, cid: str) -> None:
        with self._db() as db:
            exists = db.execute("SELECT 1 FROM conversations WHERE conversation_id=?", (cid,)).fetchone()
            if exists is None:
                raise KeyError(cid)
            db.execute("DELETE FROM chat_turns WHERE conversation_id=?", (cid,))
            db.execute("DELETE FROM conversations WHERE conversation_id=?", (cid,))

    def conversations(self, limit: int = 100) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def append(
        self, cid: str, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        tid = f"turn_{uuid4().hex}"
        with self._db() as db:
            db.execute(
                "INSERT INTO chat_turns(turn_id,conversation_id,role,content,metadata_json) VALUES (?,?,?,?,?)",
                (tid, cid, role, content, json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"), default=str)),
            )
            db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE conversation_id=?", (cid,))
        return {"turn_id": tid, "conversation_id": cid, "role": role, "content": content, "metadata": metadata or {}}

    def append_work_completion(self, cid: str, content: str, metadata: dict[str, Any]) -> dict[str, Any]:
        key = str(metadata.get("work_completion_key") or "").strip()
        if not key:
            raise ValueError("work completion key is required")
        tid = f"turn_{uuid4().hex}"
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), default=str)
        with self._db() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO chat_turns(turn_id,conversation_id,role,content,metadata_json) VALUES (?,?,?,?,?)",
                (tid, cid, "assistant", content, encoded),
            ).rowcount
            if inserted:
                db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE conversation_id=?", (cid,))
            row = db.execute(
                "SELECT * FROM chat_turns WHERE json_extract(metadata_json,'$.work_completion_key')=? LIMIT 1",
                (key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("work completion turn was not persisted")
        return {
            "turn_id": row["turn_id"], "conversation_id": row["conversation_id"], "role": row["role"],
            "content": row["content"], "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def update_work_completion(self, key: str, *, content: str | None = None, metadata_patch: dict[str, Any] | None = None) -> dict[str, Any]:
        """Upgrade one already-durable completion turn in place.

        The completion key is the idempotent identity. Reporting enrichment may
        change owner-facing prose and report provenance, never execution truth.
        """
        key = str(key or "").strip()
        if not key:
            raise ValueError("work completion key is required")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM chat_turns WHERE json_extract(metadata_json,'$.work_completion_key')=? LIMIT 1",
                (key,),
            ).fetchone()
            if row is None:
                raise KeyError(key)
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata.update(dict(metadata_patch or {}))
            next_content = row["content"] if content is None else str(content)
            db.execute(
                "UPDATE chat_turns SET content=?,metadata_json=? WHERE turn_id=?",
                (next_content, json.dumps(metadata, sort_keys=True, separators=(",", ":"), default=str), row["turn_id"]),
            )
            db.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE conversation_id=?", (row["conversation_id"],))
            updated = db.execute("SELECT * FROM chat_turns WHERE turn_id=?", (row["turn_id"],)).fetchone()
        return {
            "turn_id": updated["turn_id"], "conversation_id": updated["conversation_id"], "role": updated["role"],
            "content": updated["content"], "metadata": json.loads(updated["metadata_json"] or "{}"),
            "created_at": updated["created_at"],
        }

    def pending_work_completions(self, limit: int = 20) -> tuple[dict[str, Any], ...]:
        """Return completion turns whose deterministic report is awaiting enrichment."""
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM chat_turns
                   WHERE json_extract(metadata_json,'$.work_completion_key') IS NOT NULL
                     AND json_extract(metadata_json,'$.completion_report.mode')='deterministic_pending'
                   ORDER BY created_at,rowid LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        return tuple({
            "turn_id": row["turn_id"], "conversation_id": row["conversation_id"], "role": row["role"],
            "content": row["content"], "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
        } for row in rows)

    def owner_grounding_matches(self, source_ref: str, excerpt: str) -> bool:
        parts = str(source_ref or "").split(":", 2)
        if len(parts) != 3 or parts[0] != "chat" or not excerpt:
            return False
        _, conversation_id, turn_id = parts
        with self._db() as db:
            row = db.execute(
                "SELECT conversation_id,role,content FROM chat_turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
        return bool(
            row is not None
            and row["conversation_id"] == conversation_id
            and row["role"] == "user"
            and excerpt in row["content"]
        )

    def turns(self, cid: str, limit: int = 100) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM (SELECT rowid AS turn_order, * FROM chat_turns WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?) ORDER BY created_at, turn_order",
                (cid, limit),
            ).fetchall()
        return tuple({
            "turn_id": row["turn_id"], "conversation_id": row["conversation_id"],
            "role": row["role"], "content": row["content"],
            "metadata": json.loads(row["metadata_json"] or "{}"), "created_at": row["created_at"],
        } for row in rows)
