from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import sqlite_vec

# Production architecture declaration. A module belongs here when it owns state in
# atlas-work.db. Tests enforce that these modules never construct SQLite connections.
WORK_DATABASE_PARTICIPANTS = frozenset({
    "atlas_core.actions.store.ActionStore",
    "atlas_core.artifacts.intake.ArtifactIntakeStore",
    "atlas_core.artifacts.store.ArtifactStore",
    "atlas_core.evidence.EvidenceStore",
    "atlas_core.knowledge.generations.GenerationStore",
    "atlas_core.knowledge.passages.PassageStore",
    "atlas_core.knowledge.store.KnowledgeStore",
    "atlas_core.library.store.LibraryStore",
    "atlas_core.memory.store.MemoryStore",
    "atlas_core.work.store.WorkStore",
})


def verify_work_connection(conn: sqlite3.Connection) -> None:
    """Fail closed unless a connection satisfies atlas-work.db invariants."""
    if conn.row_factory is not sqlite3.Row:
        raise RuntimeError("atlas-work.db requires sqlite3.Row row_factory")
    if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise RuntimeError("atlas-work.db requires SQLite foreign-key enforcement")
    if int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) < 5000:
        raise RuntimeError("atlas-work.db requires busy_timeout >= 5000ms")
    try:
        conn.execute("SELECT vec_version()").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError("atlas-work.db requires the qualified sqlite-vec extension") from exc


def open_work_db(path: str | Path) -> sqlite3.Connection:
    """Construct the sole supported atlas-work.db connection shape."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
        verify_work_connection(conn)
        return conn
    except Exception:
        conn.close()
        raise


@dataclass(frozen=True)
class WorkDatabase:
    """Explicit owner of the atlas-work.db transactional domain."""

    path: Path

    def __init__(self, path: str | Path) -> None:
        object.__setattr__(self, "path", Path(path))

    def connect(self) -> sqlite3.Connection:
        return open_work_db(self.path)

    @contextmanager
    def connection(self, existing: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        if existing is not None:
            verify_work_connection(existing)
            yield existing
            return
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        """Install database-level operating mode and verify existing relationships."""
        with self.connection() as conn:
            mode = str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).casefold()
            if mode != "wal":
                raise RuntimeError(f"atlas-work.db requires WAL journal mode, got {mode}")
            conn.execute("PRAGMA synchronous=FULL")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"atlas-work.db foreign-key violations: {len(violations)}")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("atlas-work.db failed SQLite integrity_check")


def as_work_database(value: WorkDatabase | str | Path) -> WorkDatabase:
    return value if isinstance(value, WorkDatabase) else WorkDatabase(value)
