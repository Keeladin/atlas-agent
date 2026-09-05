from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from atlas_api.compose import build_runtime
from scripts.atlas_obligation_cutover import perform_cutover

def test_cutover_destroys_sqlite_state_and_preserves_non_sqlite_files(tmp_path):
    root = tmp_path / "instance"
    rt = build_runtime(root)
    cid = rt.chat_store.create_conversation("Disposable old state")["conversation_id"]
    owner = rt.identities.current_owner().principal_id
    rt.chat_store.append_owner(cid, "Old pending turn", principal_id=owner)
    payload = root / "owner-uploads" / "keep.txt"
    payload.parent.mkdir(parents=True, exist_ok=True); payload.write_text("keep payload")
    secret = root / "secrets" / "keep.secret"
    secret.parent.mkdir(parents=True, exist_ok=True); secret.write_text("keep secret")
    thumbnail_cache = root / "library-clean" / "manual" / "Thumbs.db"
    thumbnail_cache.parent.mkdir(parents=True, exist_ok=True); thumbnail_cache.write_bytes(b"thumbnail cache")
    credentials_db = root / "secrets" / "credentials.db"
    with sqlite3.connect(credentials_db) as db:
        db.execute("CREATE TABLE custody_marker(value TEXT NOT NULL)")
        db.execute("INSERT INTO custody_marker(value) VALUES ('preserve-me')")

    result = perform_cutover(root, assume_stopped=True)
    assert "rollback" not in result
    assert payload.read_text() == "keep payload"
    assert secret.read_text() == "keep secret"
    assert thumbnail_cache.read_bytes() == b"thumbnail cache"
    with sqlite3.connect(credentials_db) as db:
        assert db.execute("SELECT value FROM custody_marker").fetchone()[0] == "preserve-me"
    assert set(result["removed_sqlite_files"]) == {
        "atlas-identity.db", "atlas-work.db", "atlas-chat.db", "atlas-cadence.db",
    }
    fresh = build_runtime(root)
    assert fresh.chat_store.conversations() == ()
    assert fresh.obligation_store.list_open() == ()
    assert fresh.work_store.list() == ()
    assert fresh.cadence_store.list() == ()
    for name in ("atlas-identity.db", "atlas-work.db", "atlas-chat.db", "atlas-cadence.db"):
        with sqlite3.connect(root / name) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_cutover_refuses_wrong_instance_owner_before_reset(tmp_path, monkeypatch):
    from scripts import atlas_obligation_cutover as cutover
    root = tmp_path / "instance"
    build_runtime(root)
    existing = root / "atlas-chat.db"
    assert existing.exists()
    monkeypatch.setattr(cutover.os, "geteuid", lambda: root.stat().st_uid + 1)
    with pytest.raises(PermissionError, match="cutover must run as instance owner"):
        cutover.perform_cutover(root, assume_stopped=True)
    assert existing.exists()


def test_immutable_integrity_check_does_not_create_sqlite_sidecars(tmp_path):
    from scripts.atlas_obligation_cutover import _verify_sqlite
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as db:
        db.execute("create table marker(value text)")
        db.execute("insert into marker values ('ok')")
    _verify_sqlite(path)
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
