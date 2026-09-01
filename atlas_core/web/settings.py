from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WEB_PROVIDER_KINDS = frozenset({"jina", "brave", "tavily", "serper"})


@dataclass(frozen=True)
class WebProviderSettings:
    key: str
    kind: str
    enabled: bool
    priority: int
    credential_ref: str | None
    metadata: dict[str, Any]
    updated_at: str

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key, "kind": self.kind, "enabled": self.enabled,
            "priority": self.priority, "credential_configured": bool(self.credential_ref),
            "metadata": self.metadata, "updated_at": self.updated_at,
        }


class WebProviderSettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path); db.row_factory = sqlite3.Row; db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS web_provider_settings(
                key TEXT PRIMARY KEY, kind TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 50, credential_ref TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")

    def put(self, *, key: str, kind: str, enabled: bool = True, priority: int = 50,
            credential_ref: str | None = None, metadata: dict[str, Any] | None = None) -> WebProviderSettings:
        provider_key = str(key or "").strip()
        provider_kind = str(kind or "").strip().casefold()
        if not provider_key:
            raise ValueError("web provider key is required")
        if provider_kind not in WEB_PROVIDER_KINDS:
            raise ValueError(f"unsupported web provider kind: {provider_kind}")
        with self._db() as db:
            db.execute("""INSERT INTO web_provider_settings(key,kind,enabled,priority,credential_ref,metadata_json)
                VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET kind=excluded.kind,enabled=excluded.enabled,
                priority=excluded.priority,credential_ref=excluded.credential_ref,metadata_json=excluded.metadata_json,
                updated_at=CURRENT_TIMESTAMP""", (
                provider_key, provider_kind, 1 if enabled else 0, int(priority), credential_ref,
                json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")),
            ))
        return self.get(provider_key)

    def get(self, key: str) -> WebProviderSettings:
        with self._db() as db:
            row = db.execute("SELECT * FROM web_provider_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return _settings(row)

    def all(self) -> tuple[WebProviderSettings, ...]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM web_provider_settings ORDER BY priority DESC,key").fetchall()
        return tuple(_settings(row) for row in rows)

    def delete(self, key: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM web_provider_settings WHERE key=?", (key,))


def _settings(row: sqlite3.Row) -> WebProviderSettings:
    return WebProviderSettings(
        row["key"], row["kind"], bool(row["enabled"]), int(row["priority"]),
        row["credential_ref"], json.loads(row["metadata_json"] or "{}"), row["updated_at"],
    )
