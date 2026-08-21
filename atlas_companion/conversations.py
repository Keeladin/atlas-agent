from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_CONVERSATION_ID = "conversation_default"
_ROLES = {"user", "atlas"}


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ConversationTurn:
    id: str
    conversation_id: str
    role: str
    content: str
    task_id: str | None
    metadata: dict[str, Any]
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "task_id": self.task_id,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def title_from_message(message: str) -> str:
    text = " ".join((message or "").split())
    if not text:
        return "Ask"
    if len(text) <= 72:
        return text
    return text[:71].rstrip() + "…"


class ConversationStore:
    """Ask transcript in Companion's SQLite DB. Not a second agent."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    @contextmanager
    def _db(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT 'Ask',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'atlas')),
                    content TEXT NOT NULL,
                    task_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation
                    ON conversation_turns(conversation_id, created_at, id);
                """
            )

    def get(self, conversation_id: str) -> Conversation:
        cid = (conversation_id or "").strip()
        row = None
        if cid:
            with self._db() as db:
                row = db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown conversation: {cid or 'missing'}")
        return self._conversation_from_row(row)

    def get_or_create(self, conversation_id: str | None = None) -> Conversation:
        cid = (conversation_id or "").strip()
        if cid in {"", "current"}:
            cid = DEFAULT_CONVERSATION_ID
        with self._db() as db:
            row = db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
            if row is not None:
                return self._conversation_from_row(row)
            if cid != DEFAULT_CONVERSATION_ID:
                raise ValueError(f"Unknown conversation: {cid}")
            db.execute(
                "INSERT INTO conversations (id, title, metadata_json) VALUES (?,?,?)",
                (cid, "Ask", "{}"),
            )
            row = db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return self._conversation_from_row(row)

    def list(self) -> tuple[Conversation, ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return tuple(self._conversation_from_row(row) for row in rows)

    def turn_count(self, conversation_id: str) -> int:
        self.get(conversation_id)
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM conversation_turns WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
        return int(row["n"])

    def list_turns(self, conversation_id: str) -> tuple[ConversationTurn, ...]:
        self.get(conversation_id)
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM conversation_turns WHERE conversation_id=? ORDER BY created_at, rowid",
                (conversation_id,),
            ).fetchall()
        return tuple(self._turn_from_row(row) for row in rows)

    def add_turn(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationTurn:
        conversation = self.get(conversation_id)
        role_key = str(role or "").strip()
        if role_key not in _ROLES:
            raise ValueError(f"Unsupported conversation role: {role}")
        text = str(content or "").strip()
        if not text:
            raise ValueError("Conversation turn content must not be empty.")
        turn_id = _new_id("turn")
        task = str(task_id).strip() if task_id else None
        title = conversation.title
        if role_key == "user" and title in {"Ask", ""}:
            title = title_from_message(text)
        with self._db() as db:
            db.execute(
                """
                INSERT INTO conversation_turns
                    (id, conversation_id, role, content, task_id, metadata_json)
                VALUES (?,?,?,?,?,?)
                """,
                (turn_id, conversation.id, role_key, text, task or None, _json_dump(metadata or {})),
            )
            db.execute(
                "UPDATE conversations SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (title, conversation.id),
            )
            row = db.execute("SELECT * FROM conversation_turns WHERE id=?", (turn_id,)).fetchone()
        return self._turn_from_row(row)

    @staticmethod
    def _conversation_from_row(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            metadata=_json_load(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _turn_from_row(row: sqlite3.Row) -> ConversationTurn:
        return ConversationTurn(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            task_id=row["task_id"],
            metadata=_json_load(row["metadata_json"]),
            created_at=row["created_at"],
        )
