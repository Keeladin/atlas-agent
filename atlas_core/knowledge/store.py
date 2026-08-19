from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    title: str
    source_uri: str | None
    content_sha256: str
    chunk_count: int
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    document_id: str
    ordinal: int
    text: str
    sha256: str
    source_uri: str | None
    title: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SearchHit:
    chunk: KnowledgeChunk
    score: float


@dataclass(frozen=True)
class IngestResult:
    document: KnowledgeDocument
    created: bool


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


_SEARCH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was",
    "what", "when", "where", "which", "who", "why", "with",
}

MIN_SEARCH_OVERLAP_RATIO = 0.25
MIN_SEARCH_OVERLAP_COUNT = 2
MAX_SEARCH_RESULT_CHARS = 6_000


def content_tokens(text: str) -> tuple[str, ...]:
    tokens = re.findall(r"[\w-]+", (text or "").casefold(), flags=re.UNICODE)
    return tuple(
        token
        for token in tokens
        if token not in _SEARCH_STOPWORDS and len(token) > 2
    )


def token_overlap(query_tokens: tuple[str, ...] | set[str], text: str) -> tuple[int, float]:
    query = {token for token in query_tokens if token}
    if not query:
        return 0, 0.0
    overlap = query & set(content_tokens(text))
    return len(overlap), len(overlap) / len(query)


def hit_is_relevant(query_tokens: tuple[str, ...] | set[str], text: str) -> bool:
    query = tuple(token for token in query_tokens if token)
    if not query:
        return False
    count, ratio = token_overlap(query, text)
    if len(query) <= 2:
        return count >= 1
    return count >= MIN_SEARCH_OVERLAP_COUNT and ratio >= MIN_SEARCH_OVERLAP_RATIO


def normalize_knowledge_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def source_content_sha256(text: str) -> str:
    return hashlib.sha256(normalize_knowledge_text(text).encode("utf-8")).hexdigest()


def chunk_text(
    text: str,
    *,
    chunk_chars: int = 4000,
    overlap_chars: int = 400,
) -> tuple[str, ...]:
    if chunk_chars < 256:
        raise ValueError("chunk_chars must be >= 256")
    if overlap_chars < 0 or overlap_chars >= chunk_chars:
        raise ValueError("overlap_chars must be >= 0 and smaller than chunk_chars")
    source = normalize_knowledge_text(text)
    if not source:
        return ()

    chunks: list[str] = []
    start = 0
    length = len(source)
    while start < length:
        target = min(length, start + chunk_chars)
        end = target
        if target < length:
            floor = start + max(256, chunk_chars // 2)
            candidates = [
                source.rfind("\n\n", floor, target),
                source.rfind("\n", floor, target),
                source.rfind(" ", floor, target),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary
        piece = source[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        next_start = max(0, end - overlap_chars)
        if next_start <= start:
            next_start = end
        start = next_start
    return tuple(chunks)


class KnowledgeStore:
    """Local-first full-text knowledge and provenance store.

    FTS5 is used when the host SQLite provides it; a deterministic LIKE fallback
    preserves basic retrieval on minimal SQLite builds. Semantic/vector retrieval
    can later be added behind the same search contract when a workload earns it.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fts_enabled: bool | None = None

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
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_uri TEXT,
                    content_sha256 TEXT NOT NULL UNIQUE,
                    chunk_count INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE,
                    UNIQUE(document_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document
                    ON knowledge_chunks(document_id, ordinal);
                """
            )
            try:
                db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                    "USING fts5(chunk_id UNINDEXED, document_id UNINDEXED, title, text)"
                )
            except sqlite3.OperationalError:
                self._fts_enabled = False
            else:
                self._fts_enabled = True

    def _has_fts(self) -> bool:
        if self._fts_enabled is not None:
            return self._fts_enabled
        with self._db() as db:
            row = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
            ).fetchone()
        self._fts_enabled = row is not None
        return self._fts_enabled

    def ingest_text(
        self,
        *,
        title: str,
        text: str,
        source_uri: str | None = None,
        metadata: dict[str, Any] | None = None,
        chunk_chars: int = 4000,
        overlap_chars: int = 400,
    ) -> IngestResult:
        title = title.strip()
        if not title:
            raise ValueError("Knowledge document title must not be empty.")
        chunks = chunk_text(
            text,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
        )
        if not chunks:
            raise ValueError("Knowledge document text must not be empty.")
        content_hash = source_content_sha256(text)
        document_id = f"doc_{content_hash[:24]}"

        with self._db() as db:
            existing = db.execute(
                "SELECT * FROM knowledge_documents WHERE content_sha256=?",
                (content_hash,),
            ).fetchone()
            if existing is not None:
                return IngestResult(self._document_from_row(existing), False)

            db.execute(
                "INSERT INTO knowledge_documents "
                "(id,title,source_uri,content_sha256,chunk_count,metadata_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    document_id,
                    title,
                    source_uri,
                    content_hash,
                    len(chunks),
                    _json(metadata or {}),
                ),
            )
            for ordinal, chunk in enumerate(chunks, start=1):
                digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                chunk_id = f"chunk_{content_hash[:16]}_{ordinal:06d}"
                db.execute(
                    "INSERT INTO knowledge_chunks "
                    "(id,document_id,ordinal,text,sha256,metadata_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        chunk_id,
                        document_id,
                        ordinal,
                        chunk,
                        digest,
                        _json({"source_uri": source_uri}),
                    ),
                )
                if self._has_fts():
                    db.execute(
                        "INSERT INTO knowledge_fts (chunk_id,document_id,title,text) "
                        "VALUES (?,?,?,?)",
                        (chunk_id, document_id, title, chunk),
                    )
            row = db.execute(
                "SELECT * FROM knowledge_documents WHERE id=?",
                (document_id,),
            ).fetchone()
        return IngestResult(self._document_from_row(row), True)

    def get_document(self, document_id: str) -> KnowledgeDocument:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM knowledge_documents WHERE id=?",
                (document_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown knowledge document: {document_id}")
        return self._document_from_row(row)

    def list_documents(self, *, limit: int = 100) -> tuple[KnowledgeDocument, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("Knowledge document limit must be between 1 and 1000.")
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM knowledge_documents ORDER BY created_at DESC, id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._document_from_row(row) for row in rows)

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk:
        with self._db() as db:
            row = db.execute(
                "SELECT c.*,d.title,d.source_uri FROM knowledge_chunks c "
                "JOIN knowledge_documents d ON d.id=c.document_id "
                "WHERE c.id=?",
                (chunk_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown knowledge chunk: {chunk_id}")
        return self._chunk_from_row(row)

    def search(self, query: str, *, limit: int = 8) -> tuple[SearchHit, ...]:
        query = query.strip()
        if not query:
            raise ValueError("Knowledge search query must not be empty.")
        if limit < 1 or limit > 100:
            raise ValueError("Knowledge search limit must be between 1 and 100.")

        tokens = content_tokens(query)
        if not tokens:
            return ()
        candidate_limit = min(100, max(limit * 5, limit))

        with self._db() as db:
            if self._has_fts():
                fts_query = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
                rows = db.execute(
                    "SELECT c.*,d.title,d.source_uri,bm25(knowledge_fts) AS rank "
                    "FROM knowledge_fts "
                    "JOIN knowledge_chunks c ON c.id=knowledge_fts.chunk_id "
                    "JOIN knowledge_documents d ON d.id=c.document_id "
                    "WHERE knowledge_fts MATCH ? "
                    "ORDER BY rank ASC LIMIT ?",
                    (fts_query, candidate_limit),
                ).fetchall()
                ranked = [
                    (self._chunk_from_row(row), token_overlap(tokens, row["text"]))
                    for row in rows
                ]
            else:
                clauses = " OR ".join("lower(c.text) LIKE ?" for _ in tokens)
                params = [f"%{token}%" for token in tokens]
                rows = db.execute(
                    "SELECT c.*,d.title,d.source_uri FROM knowledge_chunks c "
                    "JOIN knowledge_documents d ON d.id=c.document_id "
                    f"WHERE {clauses} ORDER BY c.document_id,c.ordinal LIMIT ?",
                    (*params, candidate_limit),
                ).fetchall()
                ranked = [
                    (self._chunk_from_row(row), token_overlap(tokens, row["text"]))
                    for row in rows
                ]

        relevant: list[SearchHit] = []
        for chunk, (_count, ratio) in ranked:
            if not hit_is_relevant(tokens, chunk.text):
                continue
            relevant.append(SearchHit(chunk, ratio))
            if len(relevant) >= limit:
                break
        return tuple(relevant)

    @staticmethod
    def _document_from_row(row: sqlite3.Row) -> KnowledgeDocument:
        return KnowledgeDocument(
            id=row["id"],
            title=row["title"],
            source_uri=row["source_uri"],
            content_sha256=row["content_sha256"],
            chunk_count=int(row["chunk_count"]),
            metadata=_load(row["metadata_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _chunk_from_row(row: sqlite3.Row) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=row["id"],
            document_id=row["document_id"],
            ordinal=int(row["ordinal"]),
            text=row["text"],
            sha256=row["sha256"],
            source_uri=row["source_uri"],
            title=row["title"],
            metadata=_load(row["metadata_json"]),
        )
