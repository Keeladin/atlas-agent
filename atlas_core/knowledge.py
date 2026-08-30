from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, ScopeResolution


class KnowledgeStore:
    """Durable references and notes. Persistent owner memory has its own runtime."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path); db.row_factory = sqlite3.Row; db.execute("PRAGMA busy_timeout=5000")
        try:
            with db: yield db
        finally: db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            self._create_schema(db)

    @staticmethod
    def _create_schema(db: sqlite3.Connection) -> None:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_items(
            item_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK(kind IN ('reference','note')),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_ref TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(item_id UNINDEXED,title,content,tokenize='unicode61');
        CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge_items BEGIN INSERT INTO knowledge_fts(item_id,title,content) VALUES(new.item_id,new.title,new.content); END;
        CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge_items BEGIN DELETE FROM knowledge_fts WHERE item_id=old.item_id; END;
        CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge_items BEGIN DELETE FROM knowledge_fts WHERE item_id=old.item_id; INSERT INTO knowledge_fts(item_id,title,content) VALUES(new.item_id,new.title,new.content); END;
        """)

    def add(self, *, kind: str, title: str, content: str, source_ref: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        iid = f"knowledge_{uuid4().hex}"
        with self._db() as db:
            db.execute("INSERT INTO knowledge_items(item_id,kind,title,content,source_ref,metadata_json) VALUES (?,?,?,?,?,?)", (iid, kind, title, content, source_ref, json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))))
        return self.get(iid)

    def get(self, item_id: str) -> dict[str, Any]:
        with self._db() as db: row = db.execute("SELECT * FROM knowledge_items WHERE item_id=?", (item_id,)).fetchone()
        if row is None: raise KeyError(item_id)
        return {"item_id": row["item_id"], "kind": row["kind"], "title": row["title"], "content": row["content"], "source_ref": row["source_ref"], "metadata": json.loads(row["metadata_json"] or "{}"), "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def recent(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        with self._db() as db: rows = db.execute("SELECT item_id FROM knowledge_items ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(self.get(row["item_id"]) for row in rows)

    def search(self, query: str, *, limit: int = 10) -> tuple[dict[str, Any], ...]:
        q = " ".join(part for part in str(query).strip().split() if part)
        if not q: return ()
        safe = " OR ".join('"' + part.replace('"', '') + '"' for part in q.split()[:12])
        try:
            with self._db() as db:
                rows = db.execute("SELECT k.item_id,bm25(knowledge_fts) AS score FROM knowledge_fts JOIN knowledge_items k USING(item_id) WHERE knowledge_fts MATCH ? ORDER BY bm25(knowledge_fts) ASC,k.updated_at DESC LIMIT ?", (safe, max(1, min(limit, 50)))).fetchall()
        except sqlite3.OperationalError:
            return ()
        return tuple({**self.get(row["item_id"]), "score": row["score"]} for row in rows)

    def delete(self, item_id: str) -> None:
        with self._db() as db: db.execute("DELETE FROM knowledge_items WHERE item_id=?", (item_id,))


class KnowledgeRuntime:
    def __init__(self, store: KnowledgeStore, registry: CapabilityRegistry) -> None:
        self.store = store; self.registry = registry; self._register()

    def _register(self) -> None:
        schema = {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition("knowledge.search", "Search durable Atlas references and notes.", "search", "none", schema, source="knowledge", tags=("knowledge",)),
            lambda p: ScopeResolution("atlas/knowledge", dict(p), "Search durable knowledge"),
            lambda p: ActionResult(True, list(self.store.search(p["query"], limit=int(p.get("limit") or 10))), {"ok": True, "operation": "search"}),
            metadata={"scope_hint": "atlas/knowledge"},
        ), replace=True)
