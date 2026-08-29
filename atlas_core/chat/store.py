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

    def turns(self, cid: str, limit: int = 100) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM (SELECT * FROM chat_turns WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?) ORDER BY created_at, rowid",
                (cid, limit),
            ).fetchall()
        return tuple({
            "turn_id": row["turn_id"], "conversation_id": row["conversation_id"],
            "role": row["role"], "content": row["content"],
            "metadata": json.loads(row["metadata_json"] or "{}"), "created_at": row["created_at"],
        } for row in rows)
