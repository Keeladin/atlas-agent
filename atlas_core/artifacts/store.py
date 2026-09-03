from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_core.database import WorkDatabase, as_work_database

from .models import Artifact, ArtifactFacet


class ArtifactStore:
    """Durable artifact identity, provenance and governed representations.

    Registration is runtime bookkeeping about bytes an executor just handled.
    Every row cites the occurrence that established it, so the graph stays a
    projection of the action log rather than a parallel truth.
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
            CREATE TABLE IF NOT EXISTS managed_contents(
                content_sha256 TEXT PRIMARY KEY, managed_artifact_id TEXT NOT NULL UNIQUE,
                byte_size INTEGER NOT NULL, media_type TEXT, format TEXT NOT NULL,
                storage_name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS managed_content_sources(
                link_id TEXT PRIMARY KEY, source_artifact_id TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                source_root_id TEXT, source_relative_path TEXT, occurrence_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_artifact_id,content_sha256)
            );
            CREATE INDEX IF NOT EXISTS managed_source_artifact ON managed_content_sources(source_artifact_id,created_at);
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
        result = _artifact(row, facets).as_dict()
        managed = self.managed_by_artifact(artifact_id, db=db)
        if managed is not None:
            result["managed_content"] = managed
            with self._db(db) as conn:
                links = conn.execute("SELECT * FROM managed_content_sources WHERE content_sha256=? ORDER BY created_at,link_id", (managed["content_sha256"],)).fetchall()
            result["source_occurrences"] = [dict(item) for item in links]
        else:
            linked = self.managed_for_source(artifact_id) if db is None else ()
            if linked: result["managed_representations"] = list(linked)
        return result

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

    def local_facets(self, root_id: str) -> tuple[dict[str, Any], ...]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM artifact_facets WHERE kind='local_file' AND root_id=? ORDER BY relative_path",
                (root_id,),
            ).fetchall()
        return tuple(_facet(row).as_dict() for row in rows)

    # ---- managed intake / content identity

    def managed_by_hash(self, content_sha256: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._db(db) as conn:
            row = conn.execute("SELECT * FROM managed_contents WHERE content_sha256=?", (content_sha256,)).fetchone()
        return dict(row) if row is not None else None

    def managed_by_artifact(self, artifact_id: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._db(db) as conn:
            row = conn.execute("SELECT * FROM managed_contents WHERE managed_artifact_id=?", (artifact_id,)).fetchone()
        return dict(row) if row is not None else None

    def managed_for_source(self, source_artifact_id: str) -> tuple[dict[str, Any], ...]:
        with self._db() as conn:
            rows = conn.execute(
                """SELECT l.*,m.managed_artifact_id,m.byte_size,m.media_type,m.format,m.storage_name
                   FROM managed_content_sources l JOIN managed_contents m USING(content_sha256)
                   WHERE l.source_artifact_id=? ORDER BY l.created_at DESC,l.link_id DESC""",
                (source_artifact_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def register_managed_content(self, *, content_sha256: str, managed_artifact_id: str, byte_size: int,
                                 media_type: str | None, format: str, storage_name: str,
                                 db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            conn.execute(
                """INSERT INTO managed_contents(content_sha256,managed_artifact_id,byte_size,media_type,format,storage_name)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(content_sha256) DO NOTHING""",
                (content_sha256, managed_artifact_id, int(byte_size), media_type, format, storage_name),
            )
            row = conn.execute("SELECT * FROM managed_contents WHERE content_sha256=?", (content_sha256,)).fetchone()
        return dict(row)

    def link_managed_source(self, *, source_artifact_id: str, content_sha256: str, occurrence_id: str,
                            source_root_id: str | None, source_relative_path: str | None,
                            db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            conn.execute(
                """INSERT INTO managed_content_sources(link_id,source_artifact_id,content_sha256,source_root_id,source_relative_path,occurrence_id)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(source_artifact_id,content_sha256) DO NOTHING""",
                (f"managed_link_{uuid4().hex}", source_artifact_id, content_sha256, source_root_id, source_relative_path, occurrence_id),
            )
            row = conn.execute(
                "SELECT * FROM managed_content_sources WHERE source_artifact_id=? AND content_sha256=?",
                (source_artifact_id, content_sha256),
            ).fetchone()
        return dict(row)

    # ---- passive verification

    def set_facet_state(self, facet_id: str, state: str, *, db: sqlite3.Connection | None = None) -> None:
        with self._db(db) as conn:
            conn.execute("UPDATE artifact_facets SET state=? WHERE facet_id=?", (state, facet_id))

    def facet_verified(self, facet_id: str, *, observed_at: str, byte_sha256: str | None = None,
                       byte_size: int | None = None, observed: dict[str, Any] | None = None,
                       db: sqlite3.Connection | None = None) -> None:
        with self._db(db) as conn:
            conn.execute(
                """UPDATE artifact_facets SET state='present',verified_at=?,
                   byte_sha256=COALESCE(?,byte_sha256),byte_size=COALESCE(?,byte_size),
                   observed_json=CASE WHEN ? IS NULL THEN observed_json ELSE ? END WHERE facet_id=?""",
                (observed_at, byte_sha256, byte_size, None if observed is None else 1,
                 None if observed is None else json.dumps(observed, sort_keys=True, separators=(",", ":"), ensure_ascii=False), facet_id),
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
