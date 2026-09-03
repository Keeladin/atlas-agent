from __future__ import annotations

import json

from atlas_api.compose import build_runtime
from atlas_core.providers import ModelResponse


class ScriptedProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        text = self.replies.pop(0)
        return ModelResponse(text=text, provider_key="test", model="test", raw={})


def test_post_reply_auto_capture_uses_real_governed_remember(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Memory")["conversation_id"]
    provider = ScriptedProvider([
        '{"kind":"reply","reply":"Got it."}',
        '{"proposals":[{"action":"remember","title":"Units","grounding_excerpt":"I prefer metric units"}]}',
    ])
    rt.chat.provider = provider

    result = rt.chat.send(cid, "I prefer metric units", principal_id=owner.principal_id)

    assert result["turn"]["content"] == "Got it."
    memories = rt.memory_store.recent(owner.principal_id)
    assert len(memories) == 1
    assert memories[0]["content"] == "I prefer metric units"
    actions = [x for x in rt.actions_store.recent() if x.capability_id == "memory.remember"]
    assert len(actions) == 1
    assert actions[0].status == "succeeded"
    assert actions[0].scope == "atlas/memory"


def test_post_reply_capture_yes_executes_without_hijacking_reply(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    rt.policy_store.set(principal_id=owner.principal_id, scope="atlas/memory", operation="remember", decision="YES")
    cid = rt.chat_store.create_conversation("Memory")["conversation_id"]
    provider = ScriptedProvider([
        '{"kind":"reply","reply":"That makes sense."}',
        '{"proposals":[{"action":"remember","title":"Preference","grounding_excerpt":"I prefer quiet mornings"}]}',
    ])
    rt.chat.provider = provider

    result = rt.chat.send(cid, "I prefer quiet mornings", principal_id=owner.principal_id)

    assert result == {"turn": result["turn"]}
    assert result["turn"]["content"] == "That makes sense."
    actions = [x for x in rt.actions_store.recent() if x.capability_id == "memory.remember"]
    assert len(actions) == 1 and actions[0].status == "succeeded"
    assert rt.memory_store.recent(owner.principal_id)[0]["content"] == "I prefer quiet mornings"


def test_post_reply_capture_submits_even_when_policy_is_no(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    rt.policy_store.set(principal_id=owner.principal_id, scope="atlas/memory", operation="remember", decision="NO")
    cid = rt.chat_store.create_conversation("Memory")["conversation_id"]
    provider = ScriptedProvider([
        '{"kind":"reply","reply":"Okay."}',
        '{"proposals":[{"action":"remember","title":"Preference","grounding_excerpt":"I prefer tea"}]}',
    ])
    rt.chat.provider = provider

    result = rt.chat.send(cid, "I prefer tea", principal_id=owner.principal_id)

    assert result["turn"]["content"] == "Okay."
    blocked = [x for x in rt.actions_store.recent() if x.capability_id == "memory.remember"]
    assert len(blocked) == 1
    assert blocked[0].status == "blocked"
    assert rt.memory_store.recent(owner.principal_id) == ()


def test_capture_rejects_ungrounded_model_paraphrase(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Memory")["conversation_id"]
    provider = ScriptedProvider([
        '{"kind":"reply","reply":"Okay."}',
        '{"proposals":[{"action":"remember","title":"Preference","grounding_excerpt":"The owner always drinks tea"}]}',
    ])
    rt.chat.provider = provider

    rt.chat.send(cid, "I prefer tea", principal_id=owner.principal_id)

    assert rt.memory_store.recent(owner.principal_id) == ()
    assert not [x for x in rt.actions_store.recent() if x.capability_id.startswith("memory.")]
