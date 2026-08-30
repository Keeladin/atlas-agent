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

    def action_context(self, occurrence_id: str) -> dict[str, Any] | None:
        marker = f'%{occurrence_id}%'
        with self._db() as db:
            rows = db.execute(
                "SELECT rowid AS turn_order,* FROM chat_turns WHERE role='assistant' AND metadata_json LIKE ? ORDER BY rowid DESC LIMIT 50",
                (marker,),
            ).fetchall()
            for row in rows:
                metadata = json.loads(row["metadata_json"] or "{}")
                action = metadata.get("action") if isinstance(metadata, dict) else None
                if not isinstance(action, dict) or action.get("occurrence_id") != occurrence_id:
                    continue
                owner = db.execute(
                    "SELECT rowid AS turn_order,* FROM chat_turns WHERE conversation_id=? AND role='user' AND rowid<? ORDER BY rowid DESC LIMIT 1",
                    (row["conversation_id"], row["turn_order"]),
                ).fetchone()
                if owner is None:
                    return None
                return {
                    "conversation_id": row["conversation_id"],
                    "owner_turn": {
                        "turn_id": owner["turn_id"], "conversation_id": owner["conversation_id"],
                        "role": owner["role"], "content": owner["content"],
                        "metadata": json.loads(owner["metadata_json"] or "{}"), "created_at": owner["created_at"],
                    },
                }
        return None

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
