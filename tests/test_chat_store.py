from __future__ import annotations

import sqlite3

from atlas_core.chat.store import ChatStore


def test_turns_returns_latest_limited_window_in_insertion_order(tmp_path):
    store = ChatStore(tmp_path / "chat.db")
    store.initialize()
    conversation = store.create_conversation("Chat")
    cid = conversation["conversation_id"]

    store.append_owner(cid, "first", principal_id="owner-test")
    store.append(cid, "assistant", "second")
    store.append_owner(cid, "third", principal_id="owner-test")

    # SQLite CURRENT_TIMESTAMP has one-second resolution. Force a timestamp tie
    # so the query must preserve row insertion order explicitly.
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE chat_turns SET created_at='2026-08-29 16:00:00' WHERE conversation_id=?",
            (cid,),
        )

    turns = store.turns(cid, limit=2)

    assert [turn["content"] for turn in turns] == ["second", "third"]


def test_delete_conversation_removes_its_turns(tmp_path):
    store = ChatStore(tmp_path / "chat.db")
    store.initialize()
    conversation = store.create_conversation("Disposable")
    cid = conversation["conversation_id"]
    store.append_owner(cid, "temporary", principal_id="owner-test")

    store.delete_conversation(cid)

    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM conversations WHERE conversation_id=?", (cid,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM chat_turns WHERE conversation_id=?", (cid,)).fetchone()[0] == 0
