from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProviderCapabilityScore:
    provider_key: str
    capability_id: str
    score: float
    source: str
    sample_count: int | None
    updated_at: str


class ProviderScoreStore:
    """Durable measured competence scores used by model routing."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
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
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_capability_scores (
                    provider_key TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    score REAL NOT NULL CHECK (score >= 0.0 AND score <= 1.0),
                    source TEXT NOT NULL,
                    sample_count INTEGER,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (provider_key, capability_id)
                )
                """
            )

    def record(
        self,
        provider_key: str,
        capability_id: str,
        score: float,
        *,
        source: str = "eval",
        sample_count: int | None = None,
    ) -> ProviderCapabilityScore:
        provider_key = provider_key.strip()
        capability_id = capability_id.strip()
        source = source.strip() or "eval"
        if not provider_key or not capability_id:
            raise ValueError("provider_key and capability_id are required")
        if not 0.0 <= score <= 1.0:
            raise ValueError("score must be between 0 and 1")
        if sample_count is not None and sample_count < 0:
            raise ValueError("sample_count must be >= 0")
        with self._db() as db:
            db.execute(
                """
                INSERT INTO provider_capability_scores
                    (provider_key,capability_id,score,source,sample_count,updated_at)
                VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(provider_key,capability_id) DO UPDATE SET
                    score=excluded.score,
                    source=excluded.source,
                    sample_count=excluded.sample_count,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (provider_key, capability_id, float(score), source, sample_count),
            )
        result = self.get(provider_key, capability_id)
        assert result is not None
        return result

    def get(
        self,
        provider_key: str,
        capability_id: str,
    ) -> ProviderCapabilityScore | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM provider_capability_scores "
                "WHERE provider_key=? AND capability_id=?",
                (provider_key, capability_id),
            ).fetchone()
        if row is None:
            return None
        return ProviderCapabilityScore(
            provider_key=row["provider_key"],
            capability_id=row["capability_id"],
            score=float(row["score"]),
            source=row["source"],
            sample_count=(int(row["sample_count"]) if row["sample_count"] is not None else None),
            updated_at=row["updated_at"],
        )

    def list_scores(self) -> tuple[ProviderCapabilityScore, ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM provider_capability_scores "
                "ORDER BY capability_id,score DESC,provider_key"
            ).fetchall()
        return tuple(
            ProviderCapabilityScore(
                provider_key=row["provider_key"],
                capability_id=row["capability_id"],
                score=float(row["score"]),
                source=row["source"],
                sample_count=(int(row["sample_count"]) if row["sample_count"] is not None else None),
                updated_at=row["updated_at"],
            )
            for row in rows
        )
