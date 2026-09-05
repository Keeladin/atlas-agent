import os

os.environ.setdefault("ATLAS_EMBEDDING_PROVIDER", "hash")

import pytest


@pytest.fixture(autouse=True)
def _chat_obligation_test_stub(request, monkeypatch):
    """Keep chat-surface tests deterministic without depending on live model intake."""
    modules = {
        "tests.test_chat_memory_capture",
        "tests.test_chat_runtime",
        "tests.test_chat_uploads",
        "tests.test_chat_work_continuity",
        "test_chat_memory_capture",
        "test_chat_runtime",
        "test_chat_uploads",
        "test_chat_work_continuity",
    }
    if getattr(request.module, "__name__", "") not in modules:
        return
    from atlas_core.obligations import ObligationIntakeRuntime

    def capture(self, owner_turn, *, recent_context=()):
        attempts = self.store.begin_attempt(owner_turn["turn_id"])
        message = str(owner_turn.get("content") or "")
        obligations = [] if message.strip().casefold() in {"hi", "hello", "hey"} else [
            {"grounding_excerpt": message, "text": message, "kind": "communication"}
        ] if message else []
        return self.store.commit_intake(
            owner_turn["turn_id"], obligations,
            attempts=attempts, provider="test-stub", model="test-stub",
        )

    monkeypatch.setattr(ObligationIntakeRuntime, "capture", capture)

    from atlas_core.chat.runtime import ChatRuntime

    def verify_direct(self, owner_turn, candidate, relevant, tool_context):
        rows = [
            item for item in self.obligation_store.open_for_turn(owner_turn["turn_id"])
            if item.kind == "communication"
        ] if self.obligation_store is not None else []
        staged = any(item.get("capability_id") == "work.create" for item in tool_context)
        selected = {} if staged else {item.obligation_id: item.revision for item in rows}
        return selected, {
            "grounded": True,
            "fulfilled_obligation_ids": list(selected),
            "unsupported_claims": [],
            "provider": "test-stub",
            "model": "test-stub",
        }

    monkeypatch.setattr(ChatRuntime, "_verify_direct_communications", verify_direct)
