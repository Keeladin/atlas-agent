from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .passages import DEFAULT_SEGMENTER_SPEC, SEGMENTER_HEADINGS_V1, canonical_json

FTS_MECHANISM = "mechanism:fts.bm25@1"
DEFAULT_MECHANISM_SPEC = {
    "engine": "fts5.bm25", "version": 1,
    # The input-assembly spec is part of mechanism identity: it decides exactly
    # what bytes a representation is computed over.
    "assembly": {"template": "content-only@1", "fields": ["content"]},
}
DEFAULT_EXTRACTOR_CONFIG_ID = "extractor:text@1"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mechanism_input(passage: dict[str, Any], content: str, assembly: dict[str, Any]) -> tuple[str, str]:
    """Assemble a mechanism's input and return (assembled_text, input_sha256).

    Representation reuse is keyed on the COMPLETE mechanism input, never on
    content alone: an assembly template that folds in placement context (heading
    path, title) must yield distinct representations for placements that happen
    to share content, and a template change must invalidate exactly the
    representations whose assembled input changed.
    """
    fields = list(assembly.get("fields") or ["content"])
    template = str(assembly.get("template") or "content-only@1")
    locator = passage.get("locator") or {}
    parts: list[str] = []
    for field in fields:
        if field == "content":
            parts.append(content)
        elif field == "heading_path":
            parts.append(" / ".join(str(item) for item in (locator.get("heading_path") or [])))
        else:
            parts.append(str(passage.get(field) or ""))
    assembled = "\n\n".join(part for part in parts if part)
    digest = hashlib.sha256((template + " " + assembled).encode("utf-8")).hexdigest()
    return assembled, digest


class GenerationStore:
    """Index configurations and generation lifecycle.

    Exactly one generation may be active at a time, enforced in SQL. Retiring a
    generation keeps its identity, configs, memberships and verification receipt
    forever: historical retrieval evidence must stay traversable.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self, db: sqlite3.Connection | None = None):
        if db is not None:
            yield db
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS index_configs(
                config_id TEXT PRIMARY KEY,
                layer TEXT NOT NULL CHECK(layer IN ('extractor','segmenter','mechanism')),
                spec_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS generations(
                generation_id TEXT PRIMARY KEY,
                extractor_config_id TEXT NOT NULL,
                segmenter_config_id TEXT NOT NULL,
                mechanisms_json TEXT NOT NULL,
                corpus_json TEXT NOT NULL DEFAULT '{}',
                state TEXT NOT NULL CHECK(state IN ('building','verifying','candidate','active','retired','failed')),
                verification_json TEXT,
                occurrence_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                activated_at TEXT, retired_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_generation ON generations(state) WHERE state='active';
            """)

    def put_config(self, *, config_id: str, layer: str, spec: dict[str, Any],
                   db: sqlite3.Connection | None = None) -> str:
        with self._db(db) as conn:
            conn.execute(
                "INSERT INTO index_configs(config_id,layer,spec_json) VALUES (?,?,?) ON CONFLICT(config_id) DO NOTHING",
                (config_id, layer, canonical_json(spec)),
            )
        return config_id

    def config(self, config_id: str) -> dict[str, Any]:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM index_configs WHERE config_id=?", (config_id,)).fetchone()
        if row is None:
            raise KeyError(config_id)
        return {"config_id": row["config_id"], "layer": row["layer"],
                "spec": json.loads(row["spec_json"]), "created_at": row["created_at"]}

    def create(self, *, extractor_config_id: str, segmenter_config_id: str, mechanisms: list[str],
               occurrence_id: str, corpus: dict[str, Any] | None = None,
               db: sqlite3.Connection | None = None) -> str:
        generation_id = f"generation_{uuid4().hex}"
        with self._db(db) as conn:
            conn.execute(
                """INSERT INTO generations(generation_id,extractor_config_id,segmenter_config_id,mechanisms_json,
                   corpus_json,state,occurrence_id) VALUES (?,?,?,?,?,'building',?)""",
                (generation_id, extractor_config_id, segmenter_config_id, canonical_json(mechanisms),
                 canonical_json(corpus or {}), occurrence_id),
            )
        return generation_id

    def get(self, generation_id: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            row = conn.execute("SELECT * FROM generations WHERE generation_id=?", (generation_id,)).fetchone()
        if row is None:
            raise KeyError(generation_id)
        return _generation(row)

    def list(self) -> tuple[dict[str, Any], ...]:
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM generations ORDER BY created_at DESC").fetchall()
        return tuple(_generation(row) for row in rows)

    def active(self, *, db: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._db(db) as conn:
            row = conn.execute("SELECT * FROM generations WHERE state='active'").fetchone()
        return _generation(row) if row is not None else None

    def building(self, *, db: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._db(db) as conn:
            row = conn.execute(
                "SELECT * FROM generations WHERE state IN ('building','candidate') ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return _generation(row) if row is not None else None

    def set_state(self, generation_id: str, state: str, *, verification: dict[str, Any] | None = None,
                  db: sqlite3.Connection | None = None) -> dict[str, Any]:
        stamp = _iso()
        with self._db(db) as conn:
            conn.execute(
                """UPDATE generations SET state=?,
                   verification_json=COALESCE(?,verification_json),
                   activated_at=CASE WHEN ?='active' THEN ? ELSE activated_at END,
                   retired_at=CASE WHEN ?='retired' THEN ? ELSE retired_at END
                   WHERE generation_id=?""",
                (state, canonical_json(verification) if verification is not None else None,
                 state, stamp, state, stamp, generation_id),
            )
            return self.get(generation_id, db=conn)


def _generation(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "generation_id": row["generation_id"],
        "extractor_config_id": row["extractor_config_id"],
        "segmenter_config_id": row["segmenter_config_id"],
        "mechanisms": json.loads(row["mechanisms_json"] or "[]"),
        "corpus": json.loads(row["corpus_json"] or "{}"),
        "state": row["state"],
        "verification": json.loads(row["verification_json"]) if row["verification_json"] else None,
        "occurrence_id": row["occurrence_id"], "created_at": row["created_at"],
        "activated_at": row["activated_at"], "retired_at": row["retired_at"],
    }


def seed_default_configs(store: GenerationStore) -> tuple[str, str, str]:
    store.put_config(config_id=DEFAULT_EXTRACTOR_CONFIG_ID, layer="extractor", spec={"extractor": "text@1"})
    store.put_config(config_id=SEGMENTER_HEADINGS_V1, layer="segmenter", spec=DEFAULT_SEGMENTER_SPEC)
    store.put_config(config_id=FTS_MECHANISM, layer="mechanism", spec=DEFAULT_MECHANISM_SPEC)
    return DEFAULT_EXTRACTOR_CONFIG_ID, SEGMENTER_HEADINGS_V1, FTS_MECHANISM
