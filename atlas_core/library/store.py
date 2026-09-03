from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_core.database import WorkDatabase, as_work_database


class LibraryStore:
    def __init__(self, database: WorkDatabase | str | Path) -> None:
        self.database = as_work_database(database)
        self.path = self.database.path

    @contextmanager
    def _db(self):
        with self.database.connection() as db:
            yield db

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS library_scans(
                scan_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                source_roots_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
                error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS library_scan_files(
                scan_id TEXT NOT NULL, root_id TEXT NOT NULL, relative_path TEXT NOT NULL,
                byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL, media_type TEXT,
                canonical INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(scan_id,root_id,relative_path),
                FOREIGN KEY(scan_id) REFERENCES library_scans(scan_id) ON DELETE CASCADE)""")
            db.execute("CREATE INDEX IF NOT EXISTS library_scan_hash ON library_scan_files(scan_id,sha256)")
            db.execute("""CREATE TABLE IF NOT EXISTS library_reviews(
                root_id TEXT NOT NULL, relative_path TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('reviewed','approved','rejected')),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(root_id,relative_path))""")

    def create_scan(self, source_roots: list[str]) -> str:
        scan_id = f"libscan_{uuid4().hex}"
        with self._db() as db:
            db.execute(
                "INSERT INTO library_scans(scan_id,status,source_roots_json) VALUES (?,?,?)",
                (scan_id, "running", json.dumps(source_roots, separators=(",", ":"))),
            )
        return scan_id

    def add_file(self, scan_id: str, *, root_id: str, relative_path: str,
                 byte_size: int, sha256: str, media_type: str | None) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO library_scan_files(scan_id,root_id,relative_path,byte_size,sha256,media_type) VALUES (?,?,?,?,?,?)",
                (scan_id, root_id, relative_path, byte_size, sha256, media_type),
            )

    def finish_scan(self, scan_id: str, summary: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute("UPDATE library_scan_files SET canonical=0 WHERE scan_id=?", (scan_id,))
            groups = db.execute(
                "SELECT sha256 FROM library_scan_files WHERE scan_id=? GROUP BY sha256",
                (scan_id,),
            ).fetchall()
            for group in groups:
                row = db.execute(
                    """SELECT root_id,relative_path FROM library_scan_files
                       WHERE scan_id=? AND sha256=? ORDER BY root_id,relative_path LIMIT 1""",
                    (scan_id, group["sha256"]),
                ).fetchone()
                db.execute(
                    "UPDATE library_scan_files SET canonical=1 WHERE scan_id=? AND root_id=? AND relative_path=?",
                    (scan_id, row["root_id"], row["relative_path"]),
                )
            db.execute(
                "UPDATE library_scans SET status='completed',summary_json=?,completed_at=CURRENT_TIMESTAMP WHERE scan_id=?",
                (json.dumps(summary, sort_keys=True, separators=(",", ":")), scan_id),
            )

    def fail_scan(self, scan_id: str, error: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE library_scans SET status='failed',error=?,completed_at=CURRENT_TIMESTAMP WHERE scan_id=?",
                (error, scan_id),
            )

    def get_scan(self, scan_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM library_scans WHERE scan_id=?", (scan_id,)).fetchone()
        if row is None:
            raise KeyError(scan_id)
        out = dict(row)
        out["source_roots"] = json.loads(out.pop("source_roots_json"))
        out["summary"] = json.loads(out.pop("summary_json") or "{}")
        return out

    def recent_scans(self, limit: int = 25) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT scan_id FROM library_scans ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return tuple(self.get_scan(row["scan_id"]) for row in rows)

    def files(self, scan_id: str, *, canonical_only: bool = False) -> tuple[dict[str, Any], ...]:
        sql = "SELECT * FROM library_scan_files WHERE scan_id=?"
        params: list[Any] = [scan_id]
        if canonical_only:
            sql += " AND canonical=1"
        sql += " ORDER BY sha256,root_id,relative_path"
        with self._db() as db:
            rows = db.execute(sql, params).fetchall()
        return tuple({**dict(row), "canonical": bool(row["canonical"])} for row in rows)

    def duplicate_groups(self, scan_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.files(scan_id)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["sha256"], []).append(row)
        return tuple(
            {"sha256": digest, "copies": copies, "duplicate_count": len(copies) - 1}
            for digest, copies in grouped.items() if len(copies) > 1
        )

    def set_review(self, *, root_id: str, relative_path: str, status: str | None) -> dict[str, Any]:
        if status not in {None, "reviewed", "approved", "rejected"}:
            raise ValueError("unsupported library review status")
        with self._db() as db:
            if status is None:
                db.execute("DELETE FROM library_reviews WHERE root_id=? AND relative_path=?", (root_id, relative_path))
                return {"root_id": root_id, "relative_path": relative_path, "status": "unreviewed"}
            db.execute("""INSERT INTO library_reviews(root_id,relative_path,status,updated_at)
                VALUES (?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(root_id,relative_path) DO UPDATE SET status=excluded.status,updated_at=CURRENT_TIMESTAMP""",
                (root_id, relative_path, status))
            row = db.execute("SELECT * FROM library_reviews WHERE root_id=? AND relative_path=?", (root_id, relative_path)).fetchone()
        return dict(row)

    def reviews(self, *, root_id: str | None = None) -> tuple[dict[str, Any], ...]:
        sql = "SELECT * FROM library_reviews"
        params: tuple[Any, ...] = ()
        if root_id is not None:
            sql += " WHERE root_id=?"; params = (root_id,)
        sql += " ORDER BY updated_at DESC,relative_path"
        with self._db() as db:
            rows = db.execute(sql, params).fetchall()
        return tuple(dict(row) for row in rows)
