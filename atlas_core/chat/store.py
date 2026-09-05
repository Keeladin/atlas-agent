from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


_INTAKE_STATES = {"complete", "partial", "failed"}


def _turn_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = {
        "turn_id": row["turn_id"], "conversation_id": row["conversation_id"],
        "role": row["role"], "content": row["content"],
        "metadata": json.loads(row["metadata_json"] or "{}"), "created_at": row["created_at"],
    }
    if row["role"] == "user":
        result.update({
            "owner_principal_id": row["owner_principal_id"], "intake_status": row["intake_status"],
            "intake_schema_version": row["intake_schema_version"], "intake_attempts": row["intake_attempts"],
            "intake_provider": row["intake_provider"], "intake_model": row["intake_model"],
            "intake_error_code": row["intake_error_code"],
            "unmapped_spans": json.loads(row["intake_unmapped_spans_json"] or "[]"),
            "turn_completed_at": row["turn_completed_at"],
            "response_handed_off_at": row["response_handed_off_at"],
        })
    return result


class ChatStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            existing = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_turns'"
            ).fetchone()
            if existing is not None:
                columns = {row[1] for row in db.execute("PRAGMA table_info(chat_turns)")}
                required = {"owner_principal_id", "intake_status", "intake_schema_version"}
                if not required.issubset(columns):
                    raise RuntimeError("atlas-chat.db requires development schema reset for obligation intake")
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
                owner_principal_id TEXT,
                intake_status TEXT,
                intake_schema_version INTEGER,
                intake_attempts INTEGER NOT NULL DEFAULT 0,
                intake_provider TEXT,
                intake_model TEXT,
                intake_error_code TEXT,
                intake_unmapped_spans_json TEXT NOT NULL DEFAULT '[]',
                turn_completed_at TEXT,
                response_handed_off_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                CHECK(CASE WHEN role='user' THEN
                    owner_principal_id IS NOT NULL
                    AND COALESCE(intake_status IN ('complete','partial','failed'),0)=1
                    AND COALESCE(intake_schema_version=1,0)=1
                ELSE
                    owner_principal_id IS NULL AND intake_status IS NULL
                    AND intake_schema_version IS NULL
                END)
            );
            CREATE INDEX IF NOT EXISTS chat_turns_conversation
                ON chat_turns(conversation_id,created_at);
            CREATE TABLE IF NOT EXISTS response_handoff_events(
                handoff_id TEXT PRIMARY KEY,
                owner_turn_id TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL CHECK(source='asgi_final_body'),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(owner_turn_id) REFERENCES chat_turns(turn_id) ON DELETE RESTRICT
            );
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

    def append_owner(
        self, cid: str, content: str, *, principal_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        principal_id = str(principal_id or "").strip()
        if not principal_id:
            raise ValueError("authenticated owner principal is required")
        tid = f"turn_{uuid4().hex}"
        encoded = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"), default=str)
        with self._db() as db:
            db.execute(
                """INSERT INTO chat_turns(
                       turn_id,conversation_id,role,content,metadata_json,owner_principal_id,
                       intake_status,intake_schema_version,intake_error_code
                   ) VALUES (?,?,?,?,?,?,'failed',1,'intake_not_completed')""",
                (tid, cid, "user", content, encoded, principal_id),
            )
            db.execute(
                "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE conversation_id=?", (cid,)
            )
            row = db.execute("SELECT * FROM chat_turns WHERE turn_id=?", (tid,)).fetchone()
        return _turn_dict(row)

    def append(
        self, cid: str, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if role == "user":
            raise ValueError("authenticated owner turns must use append_owner")
        tid = f"turn_{uuid4().hex}"
        encoded = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"), default=str)
        with self._db() as db:
            db.execute(
                "INSERT INTO chat_turns(turn_id,conversation_id,role,content,metadata_json) VALUES (?,?,?,?,?)",
                (tid, cid, role, content, encoded),
            )
            db.execute(
                "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE conversation_id=?", (cid,)
            )
            row = db.execute("SELECT * FROM chat_turns WHERE turn_id=?", (tid,)).fetchone()
        return _turn_dict(row)

    def turn(self, turn_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM chat_turns WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            raise KeyError(turn_id)
        return _turn_dict(row)

    def mark_turn_completed(self, turn_id: str) -> dict[str, Any]:
        with self._db() as db:
            changed = db.execute(
                """UPDATE chat_turns SET turn_completed_at=COALESCE(turn_completed_at,CURRENT_TIMESTAMP)
                   WHERE turn_id=? AND role='user'""", (turn_id,)
            ).rowcount
            if changed != 1:
                raise KeyError(turn_id)
        return self.turn(turn_id)

    def mark_response_handed_off(self, turn_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute(
                "SELECT role,turn_completed_at FROM chat_turns WHERE turn_id=?", (turn_id,)
            ).fetchone()
            if row is None or row["role"] != "user":
                raise KeyError(turn_id)
            if row["turn_completed_at"] is None:
                raise ValueError("response handoff cannot precede semantic turn completion")
            db.execute(
                """INSERT OR IGNORE INTO response_handoff_events(handoff_id,owner_turn_id,source)
                   VALUES (?,?,'asgi_final_body')""",
                (f"handoff_{uuid4().hex}", turn_id),
            )
            db.execute(
                """UPDATE chat_turns SET response_handed_off_at=COALESCE(response_handed_off_at,CURRENT_TIMESTAMP)
                   WHERE turn_id=?""", (turn_id,)
            )
        return self.turn(turn_id)

    def clear_unproven_handoff(self, turn_id: str) -> dict[str, Any]:
        """Repair only a handoff stamp that lacks its required ASGI provenance event."""
        with self._db() as db:
            row=db.execute("SELECT role,response_handed_off_at FROM chat_turns WHERE turn_id=?",(turn_id,)).fetchone()
            if row is None or row["role"]!="user":raise KeyError(turn_id)
            event=db.execute("SELECT 1 FROM response_handoff_events WHERE owner_turn_id=?",(turn_id,)).fetchone()
            if event is not None:raise ValueError("proven handoff cannot be cleared by quarantine repair")
            if row["response_handed_off_at"] is None:return self.turn(turn_id)
            db.execute("UPDATE chat_turns SET response_handed_off_at=NULL WHERE turn_id=?",(turn_id,))
        return self.turn(turn_id)

    def invalid_owner_intakes(self) -> tuple[str, ...]:
        with self._db() as db:
            rows = db.execute(
                """SELECT turn_id FROM chat_turns
                   WHERE role='user' AND (
                       owner_principal_id IS NULL OR COALESCE(intake_schema_version=1,0)=0
                       OR COALESCE(intake_status IN ('complete','partial','failed'),0)=0
                   )"""
            ).fetchall()
        return tuple(row["turn_id"] for row in rows)

    def owner_grounding_matches(self, source_ref: str, excerpt: str) -> bool:
        parts = str(source_ref or "").split(":", 2)
        if len(parts) != 3 or parts[0] != "chat" or not excerpt:
            return False
        _, conversation_id, turn_id = parts
        with self._db() as db:
            row = db.execute(
                "SELECT conversation_id,role,content FROM chat_turns WHERE turn_id=?", (turn_id,)
            ).fetchone()
        return bool(
            row is not None and row["conversation_id"] == conversation_id
            and row["role"] == "user" and excerpt in row["content"]
        )

    def turns(self, cid: str, limit: int = 100) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM (
                       SELECT rowid AS turn_order,* FROM chat_turns
                       WHERE conversation_id=?
                       ORDER BY created_at DESC,rowid DESC LIMIT ?
                   ) ORDER BY created_at,turn_order""",
                (cid, limit),
            ).fetchall()
        return tuple(_turn_dict(row) for row in rows)
