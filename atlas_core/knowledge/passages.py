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

_WS = re.compile(r"[ \t]+")

SEGMENTER_HEADINGS_V1 = "segmenter:headings@1"
DEFAULT_SEGMENTER_SPEC = {"strategy": "headings", "version": 1, "max_chars": 2000, "min_chars": 200}


def normalize_passage_text(value: str) -> str:
    """Deterministic normalization for content identity.

    NFC + horizontal-whitespace collapse + trim. Case is preserved: retrieval
    text is shown to people and to the model, and casing carries meaning.
    """
    text = unicodedata.normalize("NFC", str(value))
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def content_hash(value: str) -> str:
    return hashlib.sha256(normalize_passage_text(value).encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def locator_hash(locator: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(locator).encode("utf-8")).hexdigest()


def segment(text: str, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Structural segmentation. Deterministic; never model judgement.

    Splits on markdown headings, then hard-wraps oversized blocks on paragraph
    boundaries. Locators are structural so a passage can always be pointed at.
    """
    config = {**DEFAULT_SEGMENTER_SPEC, **(spec or {})}
    max_chars = int(config["max_chars"])
    normalized = normalize_passage_text(text)
    if not normalized:
        return []
    heading = re.compile(r"^(#{1,6})\s+(.*)$")
    blocks: list[tuple[list[str], int, list[str]]] = []
    path: list[str] = []
    current: list[str] = []
    start = 0
    offset = 0
    for line in normalized.split("\n"):
        match = heading.match(line)
        if match:
            if current:
                blocks.append((list(path), start, current))
            depth = len(match.group(1))
            path = path[: depth - 1] + [match.group(2).strip()]
            current = []
            start = offset + len(line) + 1
        else:
            current.append(line)
        offset += len(line) + 1
    if current:
        blocks.append((list(path), start, current))

    passages: list[dict[str, Any]] = []
    for heading_path, block_start, lines in blocks:
        body = "\n".join(lines).strip()
        if not body:
            continue
        pieces: list[str] = []
        if len(body) <= max_chars:
            pieces = [body]
        else:
            buffer = ""
            for paragraph in body.split("\n\n"):
                candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
                if len(candidate) > max_chars and buffer:
                    pieces.append(buffer)
                    buffer = paragraph
                else:
                    buffer = candidate
            while len(buffer) > max_chars:
                pieces.append(buffer[:max_chars])
                buffer = buffer[max_chars:]
            if buffer.strip():
                pieces.append(buffer)
        cursor = block_start
        for ordinal, piece in enumerate(pieces):
            content = piece.strip()
            if not content:
                continue
            passages.append({
                "content": content,
                "locator": {
                    "heading_path": list(heading_path), "ordinal": ordinal,
                    "char_start": cursor, "char_end": cursor + len(piece),
                },
            })
            cursor += len(piece)
    return passages


class PassageStore:
    """Content units (values) and passages (placements), kept deliberately apart.

    A content unit is text stored once, addressed by hash. A passage is one
    occurrence of that content in one artifact at one locator. Reuse happens at
    the content level; provenance lives at the placement level.
    """

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
            CREATE TABLE IF NOT EXISTS passage_contents(
                content_hash TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                byte_length INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS passages(
                passage_id TEXT PRIMARY KEY,
                source_artifact_id TEXT NOT NULL,
                extraction_artifact_id TEXT NOT NULL,
                segmenter_config_id TEXT NOT NULL,
                locator_json TEXT NOT NULL,
                locator_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL REFERENCES passage_contents(content_hash),
                occurrence_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(extraction_artifact_id,segmenter_config_id,locator_hash)
            );
            CREATE INDEX IF NOT EXISTS passage_source ON passages(source_artifact_id);
            CREATE INDEX IF NOT EXISTS passage_content ON passages(content_hash);
            CREATE TABLE IF NOT EXISTS generation_passages(
                generation_id TEXT NOT NULL,
                passage_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('current','superseded')) DEFAULT 'current',
                PRIMARY KEY(generation_id,passage_id)
            );
            CREATE INDEX IF NOT EXISTS generation_passage_state ON generation_passages(generation_id,state);
            CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(content_hash UNINDEXED,text,tokenize='unicode61');
            """)

    def upsert_content(self, text: str, *, db: sqlite3.Connection | None = None) -> tuple[str, bool]:
        digest = content_hash(text)
        stored = normalize_passage_text(text)
        with self._db(db) as conn:
            existing = conn.execute("SELECT 1 FROM passage_contents WHERE content_hash=?", (digest,)).fetchone()
            if existing is not None:
                return digest, False
            conn.execute(
                "INSERT INTO passage_contents(content_hash,text,byte_length) VALUES (?,?,?)",
                (digest, stored, len(stored.encode("utf-8"))),
            )
            conn.execute("INSERT INTO passage_fts(content_hash,text) VALUES (?,?)", (digest, stored))
        return digest, True

    def add_passage(self, *, source_artifact_id: str, extraction_artifact_id: str, segmenter_config_id: str,
                    locator: dict[str, Any], content_hash_value: str, occurrence_id: str,
                    db: sqlite3.Connection | None = None) -> tuple[str, bool]:
        digest = locator_hash(locator)
        with self._db(db) as conn:
            existing = conn.execute(
                "SELECT passage_id FROM passages WHERE extraction_artifact_id=? AND segmenter_config_id=? AND locator_hash=?",
                (extraction_artifact_id, segmenter_config_id, digest),
            ).fetchone()
            if existing is not None:
                return existing["passage_id"], False
            passage_id = f"passage_{uuid4().hex}"
            conn.execute(
                """INSERT INTO passages(passage_id,source_artifact_id,extraction_artifact_id,segmenter_config_id,
                   locator_json,locator_hash,content_hash,occurrence_id) VALUES (?,?,?,?,?,?,?,?)""",
                (passage_id, source_artifact_id, extraction_artifact_id, segmenter_config_id,
                 canonical_json(locator), digest, content_hash_value, occurrence_id),
            )
        return passage_id, True

    def link(self, generation_id: str, passage_id: str, *, state: str = "current",
             db: sqlite3.Connection | None = None) -> None:
        with self._db(db) as conn:
            conn.execute(
                "INSERT INTO generation_passages(generation_id,passage_id,state) VALUES (?,?,?) "
                "ON CONFLICT(generation_id,passage_id) DO UPDATE SET state=excluded.state",
                (generation_id, passage_id, state),
            )

    def get(self, passage_id: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            row = conn.execute(
                "SELECT p.*,c.text FROM passages p JOIN passage_contents c USING(content_hash) WHERE p.passage_id=?",
                (passage_id,),
            ).fetchone()
        if row is None:
            raise KeyError(passage_id)
        return _passage(row)

    def for_source(self, source_artifact_id: str) -> tuple[dict[str, Any], ...]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT p.*,c.text FROM passages p JOIN passage_contents c USING(content_hash) "
                "WHERE p.source_artifact_id=? ORDER BY p.created_at,p.passage_id",
                (source_artifact_id,),
            ).fetchall()
        return tuple(_passage(row) for row in rows)

    def search(self, generation_id: str, query: str, *, limit: int = 10, state: str = "current",
               artifact_id: str | None = None) -> tuple[dict[str, Any], ...]:
        text = " ".join(part for part in str(query).strip().split() if part)
        if not text:
            return ()
        safe = " OR ".join('"' + part.replace('"', '') + '"' for part in text.split()[:12])
        sql = """SELECT p.*,c.text,bm25(passage_fts) AS score
                 FROM passage_fts
                 JOIN passage_contents c ON c.content_hash=passage_fts.content_hash
                 JOIN passages p ON p.content_hash=c.content_hash
                 JOIN generation_passages g ON g.passage_id=p.passage_id
                 WHERE passage_fts MATCH ? AND g.generation_id=?"""
        args: list[Any] = [safe, generation_id]
        if state != "all":
            sql += " AND g.state=?"; args.append(state)
        if artifact_id:
            sql += " AND p.source_artifact_id=?"; args.append(artifact_id)
        sql += " ORDER BY bm25(passage_fts) ASC,p.created_at ASC LIMIT ?"
        args.append(max(1, min(int(limit), 50)))
        try:
            with self._db() as conn:
                rows = conn.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return ()
        return tuple({**_passage(row), "score": row["score"]} for row in rows)

    def supersede_source(self, generation_id: str, source_artifact_id: str, *,
                         db: sqlite3.Connection | None = None) -> int:
        with self._db(db) as conn:
            return conn.execute(
                """UPDATE generation_passages SET state='superseded'
                   WHERE generation_id=? AND passage_id IN (SELECT passage_id FROM passages WHERE source_artifact_id=?)""",
                (generation_id, source_artifact_id),
            ).rowcount

    def rebuild_fts(self) -> int:
        """The physical index is disposable-derived: rebuildable from content units alone."""
        with self._db() as conn:
            conn.execute("DELETE FROM passage_fts")
            conn.execute("INSERT INTO passage_fts(content_hash,text) SELECT content_hash,text FROM passage_contents")
            return int(conn.execute("SELECT COUNT(*) FROM passage_fts").fetchone()[0])


def _passage(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "passage_id": row["passage_id"], "source_artifact_id": row["source_artifact_id"],
        "extraction_artifact_id": row["extraction_artifact_id"],
        "segmenter_config_id": row["segmenter_config_id"],
        "locator": json.loads(row["locator_json"] or "{}"), "content_hash": row["content_hash"],
        "content": row["text"], "occurrence_id": row["occurrence_id"], "created_at": row["created_at"],
    }
