from __future__ import annotations

import json

from atlas_core.chat.runtime import ChatRuntime
from atlas_core.chat.store import ChatStore
from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.evidence import EvidenceStore
from atlas_core.knowledge import KnowledgeStore
from atlas_core.memory import MemoryRuntime, MemoryStore
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.identity import IdentityStore
from atlas_core.providers import ModelResponse


class CapturingProvider:
    def __init__(self) -> None:
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ModelResponse(
            text='{"kind":"reply","reply":"Hey"}',
            provider_key="test",
            model="test-model",
            raw={},
        )


def test_chat_system_prompt_preserves_atlas_conversational_identity(tmp_path):
    store = ChatStore(tmp_path / "chat.db")
    store.initialize()
    conversation = store.create_conversation("Chat")
    cid = conversation["conversation_id"]
    store.append(cid, "user", "hi atlas")
    provider = CapturingProvider()
    identities = IdentityStore(tmp_path / "identity.db")
    identities.initialize(owner_display_name="Jaco")
    owner = identities.current_owner()
    runtime = ChatRuntime(store, provider, None, None, None, None, identities)

    decision = runtime._decision(cid, "hi atlas", [], [], [], owner.principal_id)

    assert decision == {"kind": "reply", "reply": "Hey"}
    assert len(provider.requests) == 1
    system = provider.requests[0].system
    assert "one persistent operational companion" in system
    assert "Do not re-introduce yourself" in system
    assert "generic onboarding/support language" in system
    assert "respond briefly and naturally" in system
    assert "never claim memory, state, evidence or outcomes" in system
    assert "owner_identity is authenticated durable runtime truth" in system
    assert "Do not equate the current conversation window" in system
    assert "Durable Memory and durable Knowledge are separate runtime responsibilities" in system
    assert "use memory.search when available" in system
    assert "memory.remember, memory.update and memory.retract" in system
    assert "CLAUDE.md" not in system
    prompt = json.loads(provider.requests[0].input)
    assert prompt["owner_identity"] == {"kind": "human", "display_name": "Jaco"}


class SequenceProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        text = self.responses.pop(0)
        return ModelResponse(text=text, provider_key="test", model="test-model", raw={})


def _chat_with_memory(tmp_path, capture_decision: str):
    identity_db = tmp_path / "identity.db"
    work_db = tmp_path / "work.db"
    identities = IdentityStore(identity_db); identities.initialize(owner_display_name="Jaco")
    owner = identities.current_owner()
    policy_store = PolicyStore(identity_db); policy_store.initialize()
    action_store = ActionStore(work_db); action_store.initialize()
    evidence = EvidenceStore(work_db); evidence.initialize()
    registry = CapabilityRegistry(); policy = OwnerPolicy(policy_store)
    actions = ActionRuntime(policy=policy, store=action_store, evidence=evidence, executor_resolver=registry.executor)
    capabilities = CapabilityRuntime(registry, actions, policy)
    memory_store = MemoryStore(work_db); memory_store.initialize(); MemoryRuntime(memory_store, registry, action_store)
    knowledge_store = KnowledgeStore(work_db); knowledge_store.initialize()
    policy_store.set(principal_id=owner.principal_id, scope="atlas/memory", operation="remember", decision=capture_decision)
    chat_store = ChatStore(tmp_path / "chat.db"); chat_store.initialize(); conversation = chat_store.create_conversation("Chat")
    provider = SequenceProvider(
        '{"kind":"reply","reply":"Got it."}',
        '{"proposals":[{"action":"remember","title":"Units","grounding_excerpt":"I prefer metric units"}]}',
    )
    runtime = ChatRuntime(chat_store, provider, registry, capabilities, knowledge_store, memory_store, identities)
    return runtime, conversation["conversation_id"], owner, action_store


def test_post_reply_auto_capture_confirm_does_not_hijack_turn(tmp_path):
    runtime, cid, owner, action_store = _chat_with_memory(tmp_path, "CONFIRM")
    result = runtime.send(cid, "I prefer metric units", principal_id=owner.principal_id)
    assert result["turn"]["content"] == "Got it."
    assert result["turn"]["metadata"] == {"tools_used": []}
    assert "action" not in result
    pending = action_store.pending(principal_id=owner.principal_id)
    assert len(pending) == 1
    assert pending[0].capability_id == "memory.remember"
    assert pending[0].payload["content"] == "I prefer metric units"


def test_post_reply_auto_capture_no_still_creates_blocked_evidence(tmp_path):
    runtime, cid, owner, action_store = _chat_with_memory(tmp_path, "NO")
    result = runtime.send(cid, "I prefer metric units", principal_id=owner.principal_id)
    assert result["turn"]["content"] == "Got it."
    assert result["turn"]["metadata"] == {"tools_used": []}
    rows = [row for row in action_store.recent(limit=20) if row.capability_id == "memory.remember"]
    assert len(rows) == 1
    assert rows[0].status == "blocked"
    assert rows[0].policy_decision == "NO"
