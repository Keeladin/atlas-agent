from __future__ import annotations

import sqlite3

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

    result = perform_cutover(root, assume_stopped=True)
    assert "rollback" not in result
    assert payload.read_text() == "keep payload"
    assert secret.read_text() == "keep secret"
    fresh = build_runtime(root)
    assert fresh.chat_store.conversations() == ()
    assert fresh.obligation_store.list_open() == ()
    assert fresh.work_store.list() == ()
    assert fresh.cadence_store.list() == ()
    for name in ("atlas-identity.db", "atlas-work.db", "atlas-chat.db", "atlas-cadence.db"):
        with sqlite3.connect(root / name) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
