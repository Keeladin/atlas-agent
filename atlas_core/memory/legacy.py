from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_core.database import WorkDatabase, as_work_database

from .models import MemoryItem

_WS = re.compile(r"\s+")


def normalize_memory_text(value: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", str(value))).strip().casefold()


def memory_content_hash(value: str) -> str:
    return hashlib.sha256(normalize_memory_text(value).encode("utf-8")).hexdigest()


class LegacyMemoryStore:
    def __init__(self, database: WorkDatabase | str | Path) -> None:
        self.database = as_work_database(database)
        self.path = self.database.path

    @contextmanager
    def _db(self, db: sqlite3.Connection | None = None):
        with self.database.connection(db) as conn:
            yield conn

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS memory_items (
                item_id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                grounding_excerpt TEXT,
                source_ref TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','superseded','retracted')),
                supersedes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                retracted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS memory_principal_state ON memory_items(principal_id,state,updated_at);
            CREATE INDEX IF NOT EXISTS memory_supersedes ON memory_items(principal_id,supersedes);
            CREATE INDEX IF NOT EXISTS memory_principal_content_hash ON memory_items(principal_id,content_hash,state);
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(item_id UNINDEXED,title,content,tokenize='unicode61');
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_items BEGIN
                INSERT INTO memory_fts(item_id,title,content) VALUES(new.item_id,new.title,new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_items BEGIN
                DELETE FROM memory_fts WHERE item_id=old.item_id;
            END;
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory_items BEGIN
                DELETE FROM memory_fts WHERE item_id=old.item_id;
                INSERT INTO memory_fts(item_id,title,content) VALUES(new.item_id,new.title,new.content);
            END;
            """)
            db.executescript("""
            CREATE TRIGGER IF NOT EXISTS memory_duplicate_active_insert
            BEFORE INSERT ON memory_items
            WHEN NEW.state='active' AND EXISTS (
                SELECT 1 FROM memory_items
                WHERE principal_id=NEW.principal_id AND state='active' AND content_hash=NEW.content_hash
            )
            BEGIN SELECT RAISE(ABORT, 'duplicate active memory'); END;
            CREATE TRIGGER IF NOT EXISTS memory_duplicate_active_update
            BEFORE UPDATE OF state,content_hash,principal_id ON memory_items
            WHEN NEW.state='active' AND EXISTS (
                SELECT 1 FROM memory_items
                WHERE principal_id=NEW.principal_id AND state='active' AND content_hash=NEW.content_hash AND item_id!=OLD.item_id
            )
            BEGIN SELECT RAISE(ABORT, 'duplicate active memory'); END;
            """)

    def add(self, *, principal_id: str, title: str = "Memory", content: str,
            grounding_excerpt: str | None = None, source_ref: str | None = None,
            metadata: dict[str, Any] | None = None, supersedes: str | None = None,
            db: sqlite3.Connection | None = None) -> dict[str, Any]:
        iid = f"memory_{uuid4().hex}"
        with self._db(db) as conn:
            if supersedes:
                prior = conn.execute("SELECT item_id,state FROM memory_items WHERE item_id=? AND principal_id=?", (supersedes, principal_id)).fetchone()
                if prior is None:
                    raise KeyError(supersedes)
                conn.execute("UPDATE memory_items SET state='superseded',updated_at=CURRENT_TIMESTAMP WHERE item_id=?", (supersedes,))
            conn.execute(
                "INSERT INTO memory_items(item_id,principal_id,title,content,content_hash,grounding_excerpt,source_ref,metadata_json,supersedes) VALUES (?,?,?,?,?,?,?,?,?)",
                (iid, principal_id, title or "Memory", content, memory_content_hash(content), grounding_excerpt, source_ref,
                 json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False), supersedes),
            )
            row = conn.execute("SELECT * FROM memory_items WHERE item_id=?", (iid,)).fetchone()
        return _item(row).as_dict()

    def update(self, *, principal_id: str, item_id: str, content: str, title: str | None = None,
               grounding_excerpt: str | None = None, source_ref: str | None = None,
               metadata: dict[str, Any] | None = None, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            prior = conn.execute("SELECT * FROM memory_items WHERE item_id=? AND principal_id=?", (item_id, principal_id)).fetchone()
            if prior is None:
                raise KeyError(item_id)
            if prior["state"] == "superseded":
                raise ValueError("cannot update a superseded memory")
            return self.add(
                principal_id=principal_id, title=title or prior["title"], content=content,
                grounding_excerpt=grounding_excerpt, source_ref=source_ref,
                metadata=metadata if metadata is not None else json.loads(prior["metadata_json"] or "{}"),
                supersedes=item_id, db=conn,
            )

    def get(self, principal_id: str, item_id: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE item_id=? AND principal_id=?", (item_id, principal_id)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return _item(row).as_dict()

    def recent(self, principal_id: str, *, limit: int = 100, include_history: bool = False) -> tuple[dict[str, Any], ...]:
        where = "principal_id=?" if include_history else "principal_id=? AND state!='superseded'"
        with self._db() as db:
            rows = db.execute(f"SELECT * FROM memory_items WHERE {where} ORDER BY updated_at DESC,item_id DESC LIMIT ?", (principal_id, max(1, min(int(limit), 500)))).fetchall()
        return tuple(_item(row).as_dict() for row in rows)

    def search(self, principal_id: str, query: str, *, limit: int = 10) -> tuple[dict[str, Any], ...]:
        q = " ".join(part for part in str(query).strip().split() if part)
        if not q:
            return ()
        safe = " OR ".join('"' + part.replace('"', '') + '"' for part in q.split()[:12])
        try:
            with self._db() as db:
                rows = db.execute(
                    """SELECT m.*,bm25(memory_fts) AS score
                    FROM memory_fts JOIN memory_items m USING(item_id)
                    WHERE m.principal_id=? AND m.state='active' AND memory_fts MATCH ?
                    ORDER BY bm25(memory_fts) ASC,m.updated_at DESC LIMIT ?""",
                    (principal_id, safe, max(1, min(int(limit), 50))),
                ).fetchall()
        except sqlite3.OperationalError:
            return ()
        return tuple({**_item(row).as_dict(), "score": row["score"]} for row in rows)

    def retract(self, principal_id: str, item_id: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            changed = conn.execute(
                "UPDATE memory_items SET state='retracted',retracted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE item_id=? AND principal_id=? AND state='active'",
                (item_id, principal_id),
            ).rowcount
            if changed != 1:
                row = conn.execute("SELECT state FROM memory_items WHERE item_id=? AND principal_id=?", (item_id, principal_id)).fetchone()
                if row is None: raise KeyError(item_id)
                if row["state"] == "retracted": return self.get(principal_id, item_id, db=conn)
                raise ValueError("only an active memory can be retracted")
            return self.get(principal_id, item_id, db=conn)

    def restore(self, principal_id: str, item_id: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            changed = conn.execute(
                "UPDATE memory_items SET state='active',retracted_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE item_id=? AND principal_id=? AND state='retracted'",
                (item_id, principal_id),
            ).rowcount
            if changed != 1:
                row = conn.execute("SELECT state FROM memory_items WHERE item_id=? AND principal_id=?", (item_id, principal_id)).fetchone()
                if row is None: raise KeyError(item_id)
                if row["state"] == "active": return self.get(principal_id, item_id, db=conn)
                raise ValueError("only a retracted memory can be restored")
            return self.get(principal_id, item_id, db=conn)

    def chain_for(self, principal_id: str, item_id: str) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            return self.chain(principal_id, item_id, db=db)

    def chain(self, principal_id: str, item_id: str, *, db: sqlite3.Connection) -> tuple[dict[str, Any], ...]:
        if db.execute("SELECT 1 FROM memory_items WHERE item_id=? AND principal_id=?", (item_id, principal_id)).fetchone() is None:
            raise KeyError(item_id)
        queue = [item_id]
        visited: set[str] = set()
        rows: list[dict[str, Any]] = []
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            row = db.execute("SELECT * FROM memory_items WHERE item_id=? AND principal_id=?", (current, principal_id)).fetchone()
            if row is None:
                continue
            item = _item(row).as_dict(); rows.append(item)
            prior = item.get("supersedes")
            if isinstance(prior, str) and prior and prior not in visited:
                queue.append(prior)
            for child in db.execute("SELECT item_id FROM memory_items WHERE principal_id=? AND supersedes=?", (principal_id, current)).fetchall():
                if child["item_id"] not in visited:
                    queue.append(child["item_id"])
        return tuple(rows)


def _item(row: sqlite3.Row) -> MemoryItem:
    return MemoryItem(
        item_id=row["item_id"], principal_id=row["principal_id"], title=row["title"], content=row["content"],
        grounding_excerpt=row["grounding_excerpt"], source_ref=row["source_ref"], metadata=json.loads(row["metadata_json"] or "{}"),
        state=row["state"], supersedes=row["supersedes"], created_at=row["created_at"], updated_at=row["updated_at"], retracted_at=row["retracted_at"],
    )
