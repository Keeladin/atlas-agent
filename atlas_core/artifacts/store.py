from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import Artifact, ArtifactFacet


class ArtifactStore:
    """Durable artifact identity, provenance and governed representations.

    Registration is runtime bookkeeping about bytes an executor just handled.
    Every row cites the occurrence that established it, so the graph stays a
    projection of the action log rather than a parallel truth.
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
            CREATE TABLE IF NOT EXISTS artifacts(
                artifact_id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                media_type TEXT,
                provenance_json TEXT NOT NULL DEFAULT '{}',
                occurrence_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS artifact_principal ON artifacts(principal_id,created_at);
            CREATE TABLE IF NOT EXISTS artifact_facets(
                facet_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('local_file','remote_resource')),
                root_id TEXT, relative_path TEXT, byte_sha256 TEXT, byte_size INTEGER,
                provider TEXT, external_id TEXT, locator TEXT,
                observed_json TEXT NOT NULL DEFAULT '{}',
                state TEXT NOT NULL CHECK(state IN ('present','stale','missing')) DEFAULT 'present',
                occurrence_id TEXT NOT NULL,
                verified_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS facet_local_path
                ON artifact_facets(root_id,relative_path) WHERE kind='local_file';
            CREATE INDEX IF NOT EXISTS facet_hash ON artifact_facets(byte_sha256);
            CREATE INDEX IF NOT EXISTS facet_artifact ON artifact_facets(artifact_id);
            """)

    # ---- registration (executor-internal bookkeeping)

    def register(self, *, principal_id: str, display_name: str, occurrence_id: str,
                 media_type: str | None = None, provenance: dict[str, Any] | None = None,
                 db: sqlite3.Connection | None = None) -> str:
        artifact_id = f"artifact_{uuid4().hex}"
        with self._db(db) as conn:
            conn.execute(
                "INSERT INTO artifacts(artifact_id,principal_id,display_name,media_type,provenance_json,occurrence_id) VALUES (?,?,?,?,?,?)",
                (artifact_id, principal_id, display_name, media_type,
                 json.dumps(provenance or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False), occurrence_id),
            )
        return artifact_id

    def add_facet(self, *, artifact_id: str, kind: str, occurrence_id: str,
                  root_id: str | None = None, relative_path: str | None = None,
                  byte_sha256: str | None = None, byte_size: int | None = None,
                  provider: str | None = None, external_id: str | None = None, locator: str | None = None,
                  observed: dict[str, Any] | None = None, verified_at: str | None = None,
                  db: sqlite3.Connection | None = None) -> str:
        facet_id = f"facet_{uuid4().hex}"
        with self._db(db) as conn:
            conn.execute(
                """INSERT INTO artifact_facets(facet_id,artifact_id,kind,root_id,relative_path,byte_sha256,byte_size,
                   provider,external_id,locator,observed_json,occurrence_id,verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (facet_id, artifact_id, kind, root_id, relative_path, byte_sha256, byte_size,
                 provider, external_id, locator,
                 json.dumps(observed or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                 occurrence_id, verified_at),
            )
        return facet_id

    # ---- reads

    def get(self, artifact_id: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
            if row is None:
                raise KeyError(artifact_id)
            facets = conn.execute(
                "SELECT * FROM artifact_facets WHERE artifact_id=? ORDER BY created_at,facet_id", (artifact_id,)
            ).fetchall()
        return _artifact(row, facets).as_dict()

    def list(self, principal_id: str, *, name_like: str | None = None, byte_sha256: str | None = None,
             state: str | None = None, limit: int = 100) -> tuple[dict[str, Any], ...]:
        sql = "SELECT DISTINCT a.artifact_id FROM artifacts a LEFT JOIN artifact_facets f ON f.artifact_id=a.artifact_id WHERE a.principal_id=?"
        args: list[Any] = [principal_id]
        if name_like:
            sql += " AND a.display_name LIKE ?"; args.append(f"%{name_like}%")
        if byte_sha256:
            sql += " AND f.byte_sha256=?"; args.append(byte_sha256)
        if state:
            sql += " AND f.state=?"; args.append(state)
        sql += " ORDER BY a.created_at DESC LIMIT ?"; args.append(max(1, min(int(limit), 500)))
        with self._db() as conn:
            rows = conn.execute(sql, args).fetchall()
        return tuple(self.get(row["artifact_id"]) for row in rows)

    def find_local(self, root_id: str, relative_path: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._db(db) as conn:
            row = conn.execute(
                "SELECT * FROM artifact_facets WHERE kind='local_file' AND root_id=? AND relative_path=?",
                (root_id, relative_path),
            ).fetchone()
        return _facet(row).as_dict() if row is not None else None

    def facets_for_hash(self, byte_sha256: str) -> tuple[dict[str, Any], ...]:
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM artifact_facets WHERE byte_sha256=?", (byte_sha256,)).fetchall()
        return tuple(_facet(row).as_dict() for row in rows)

    # ---- passive verification

    def set_facet_state(self, facet_id: str, state: str, *, db: sqlite3.Connection | None = None) -> None:
        with self._db(db) as conn:
            conn.execute("UPDATE artifact_facets SET state=? WHERE facet_id=?", (state, facet_id))

    def facet_verified(self, facet_id: str, *, observed_at: str, byte_sha256: str | None = None,
                       byte_size: int | None = None, db: sqlite3.Connection | None = None) -> None:
        with self._db(db) as conn:
            conn.execute(
                """UPDATE artifact_facets SET state='present',verified_at=?,
                   byte_sha256=COALESCE(?,byte_sha256),byte_size=COALESCE(?,byte_size) WHERE facet_id=?""",
                (observed_at, byte_sha256, byte_size, facet_id),
            )


def _facet(row: sqlite3.Row) -> ArtifactFacet:
    return ArtifactFacet(
        facet_id=row["facet_id"], artifact_id=row["artifact_id"], kind=row["kind"], state=row["state"],
        occurrence_id=row["occurrence_id"], root_id=row["root_id"], relative_path=row["relative_path"],
        byte_sha256=row["byte_sha256"], byte_size=row["byte_size"], provider=row["provider"],
        external_id=row["external_id"], locator=row["locator"],
        observed=json.loads(row["observed_json"] or "{}"), verified_at=row["verified_at"], created_at=row["created_at"],
    )


def _artifact(row: sqlite3.Row, facet_rows: list[sqlite3.Row]) -> Artifact:
    return Artifact(
        artifact_id=row["artifact_id"], principal_id=row["principal_id"], display_name=row["display_name"],
        media_type=row["media_type"], provenance=json.loads(row["provenance_json"] or "{}"),
        occurrence_id=row["occurrence_id"], created_at=row["created_at"],
        facets=tuple(_facet(item) for item in facet_rows),
    )
