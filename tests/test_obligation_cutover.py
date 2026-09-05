from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from atlas_api.compose import build_runtime
from scripts.atlas_obligation_cutover import MANIFEST_NAME, perform_cutover


def test_cutover_copies_full_state_then_recreates_sqlite_only(tmp_path):
    root = tmp_path / "instance"
    rt = build_runtime(root)
    cid = rt.chat_store.create_conversation("Disposable old state")["conversation_id"]
    owner = rt.identities.current_owner().principal_id
    rt.chat_store.append_owner(cid, "Old pending turn", principal_id=owner)
    payload = root / "owner-uploads" / "keep.txt"
    payload.write_text("keep payload")
    secret = root / "secrets" / "keep.secret"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("keep secret")

    result = perform_cutover(
        root, rollback_root=tmp_path / "rollbacks", assume_stopped=True
    )
    rollback = Path(result["rollback"])
    assert rollback.is_dir()
    assert (rollback / MANIFEST_NAME).is_file()
    manifest = json.loads((rollback / MANIFEST_NAME).read_text())
    assert manifest["schema"] == 3
    assert manifest["runtime_revision"]
    assert manifest["verification"] == "full_state_copy + readonly_open + pragma_integrity_check + sha256"
    assert "atlas-chat.db" in manifest["databases"]
    assert manifest["databases"]["atlas-chat.db"]["integrity_check"] == "ok"
    assert manifest["databases"]["atlas-chat.db"]["schema_sha256"]
    assert (rollback / "owner-uploads" / "keep.txt").read_text() == "keep payload"
    assert (rollback / "secrets" / "keep.secret").read_text() == "keep secret"

    assert payload.read_text() == "keep payload"
    assert secret.read_text() == "keep secret"
    fresh = build_runtime(root)
    assert fresh.chat_store.conversations() == ()
    assert fresh.obligation_store.list_open() == ()
    assert fresh.work_store.list() == ()
    assert fresh.cadence_store.list() == ()
    for name in ("atlas-identity.db", "atlas-work.db", "atlas-chat.db", "atlas-cadence.db"):
        path = root / name
        assert path.exists()
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
