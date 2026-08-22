from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
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
    pinned: bool = False
    archived_at: str | None = None


@dataclass(frozen=True)
class ConversationTurn:
    id: str
    conversation_id: str
    role: str
    content: str
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class ConversationView:
    id: str
    title: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    turn_count: int
    pinned: bool = False
    archived_at: str | None = None
    turns: tuple[ConversationTurn, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": self.turn_count,
            "pinned": self.pinned,
            "archived": self.archived_at is not None,
            "archived_at": self.archived_at,
            "turns": [
                {
                    "id": turn.id,
                    "conversation_id": turn.conversation_id,
                    "role": turn.role,
                    "content": turn.content,
                    "metadata": turn.metadata,
                    "created_at": turn.created_at,
                }
                for turn in self.turns
            ],
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
        return "Chat"
    if len(text) <= 72:
        return text
    return text[:71].rstrip() + "…"


class ConversationStore:
    """Chat transcript store. Isolated from Work persistence."""

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
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT 'Chat',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'atlas')),
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation
                    ON conversation_turns(conversation_id, created_at, id);
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "pinned" not in columns:
                db.execute(
                    "ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
                )
            if "archived_at" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN archived_at TEXT")

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
                (cid, "Chat", "{}"),
            )
            row = db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return self._conversation_from_row(row)

    def create(self, *, title: str = "Chat") -> Conversation:
        conversation_id = _new_id("conversation")
        label = " ".join((title or "").split()) or "Chat"
        with self._db() as db:
            db.execute(
                "INSERT INTO conversations (id, title, metadata_json) VALUES (?,?,?)",
                (conversation_id, label, "{}"),
            )
            row = db.execute(
                "SELECT * FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        return self._conversation_from_row(row)

    def list(self, *, archived: bool = False) -> tuple[Conversation, ...]:
        with self._db() as db:
            if archived:
                rows = db.execute(
                    """
                    SELECT * FROM conversations
                    WHERE archived_at IS NOT NULL
                    ORDER BY pinned DESC, updated_at DESC, id DESC
                    """
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT * FROM conversations
                    WHERE archived_at IS NULL
                    ORDER BY pinned DESC, updated_at DESC, id DESC
                    """
                ).fetchall()
        return tuple(self._conversation_from_row(row) for row in rows)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        self.get(conversation_id)
        label = " ".join((title or "").split()) or "Chat"
        with self._db() as db:
            db.execute(
                "UPDATE conversations SET title=? WHERE id=?",
                (label, conversation_id),
            )
            row = db.execute(
                "SELECT * FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        return self._conversation_from_row(row)

    def set_pinned(self, conversation_id: str, pinned: bool) -> Conversation:
        self.get(conversation_id)
        with self._db() as db:
            db.execute(
                "UPDATE conversations SET pinned=? WHERE id=?",
                (1 if pinned else 0, conversation_id),
            )
            row = db.execute(
                "SELECT * FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        return self._conversation_from_row(row)

    def set_archived(self, conversation_id: str, archived: bool) -> Conversation:
        self.get(conversation_id)
        with self._db() as db:
            if archived:
                db.execute(
                    "UPDATE conversations SET archived_at=CURRENT_TIMESTAMP WHERE id=?",
                    (conversation_id,),
                )
            else:
                db.execute(
                    "UPDATE conversations SET archived_at=NULL WHERE id=?",
                    (conversation_id,),
                )
            row = db.execute(
                "SELECT * FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        return self._conversation_from_row(row)

    def delete(self, conversation_id: str) -> None:
        self.get(conversation_id)
        with self._db() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

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
        title = conversation.title
        if role_key == "user" and title in {"Chat", ""}:
            title = title_from_message(text)
        with self._db() as db:
            db.execute(
                """
                INSERT INTO conversation_turns
                    (id, conversation_id, role, content, metadata_json)
                VALUES (?,?,?,?,?)
                """,
                (turn_id, conversation.id, role_key, text, _json_dump(metadata or {})),
            )
            db.execute(
                "UPDATE conversations SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (title, conversation.id),
            )
            row = db.execute("SELECT * FROM conversation_turns WHERE id=?", (turn_id,)).fetchone()
        return self._turn_from_row(row)

    def view(self, conversation_id: str, *, include_turns: bool = True) -> ConversationView:
        record = self.get(conversation_id)
        turns = self.list_turns(record.id) if include_turns else ()
        count = len(turns) if include_turns else self.turn_count(record.id)
        return ConversationView(
            id=record.id,
            title=record.title,
            metadata=record.metadata,
            created_at=record.created_at,
            updated_at=record.updated_at,
            turn_count=count,
            pinned=record.pinned,
            archived_at=record.archived_at,
            turns=turns,
        )

    @staticmethod
    def _conversation_from_row(row: sqlite3.Row) -> Conversation:
        keys = row.keys()
        archived_at = row["archived_at"] if "archived_at" in keys else None
        pinned = bool(row["pinned"]) if "pinned" in keys else False
        return Conversation(
            id=row["id"],
            title=row["title"],
            metadata=_json_load(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            pinned=pinned,
            archived_at=archived_at,
        )

    @staticmethod
    def _turn_from_row(row: sqlite3.Row) -> ConversationTurn:
        return ConversationTurn(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            metadata=_json_load(row["metadata_json"]),
            created_at=row["created_at"],
        )
