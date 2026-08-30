from __future__ import annotations

import json
import sqlite3

import pytest

from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.evidence import EvidenceStore
from atlas_core.memory import MemoryRuntime, MemoryStore
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.provenance import InvocationProvenance


def _runtime(tmp_path):
    identity_db = tmp_path / "identity.db"
    work_db = tmp_path / "work.db"
    policy_store = PolicyStore(identity_db); policy_store.initialize()
    action_store = ActionStore(work_db); action_store.initialize()
    evidence = EvidenceStore(work_db); evidence.initialize()
    registry = CapabilityRegistry()
    policy = OwnerPolicy(policy_store)
    actions = ActionRuntime(policy=policy, store=action_store, evidence=evidence, executor_resolver=registry.executor)
    capabilities = CapabilityRuntime(registry, actions, policy)
    memory_store = MemoryStore(work_db); memory_store.initialize()
    memory = MemoryRuntime(memory_store, registry, action_store)
    return policy_store, action_store, evidence, registry, capabilities, actions, memory_store, memory


def _prov(owner: str = "principal_owner", surface: str = "control") -> InvocationProvenance:
    return InvocationProvenance(owner, "human", surface)


def _set(policy_store, operation: str, decision: str, owner: str = "principal_owner") -> None:
    policy_store.set(principal_id=owner, scope="atlas/memory", operation=operation, decision=decision)


def test_operation_level_authority_does_not_collapse_to_capture(tmp_path):
    policy_store, _, _, registry, capabilities, _, _, _ = _runtime(tmp_path)
    _set(policy_store, "remember", "YES")
    remembered = capabilities.invoke("memory.remember", {"content": "I prefer metric units."}, provenance=_prov())
    assert remembered.status == "succeeded"
    item_id = remembered.result["item_id"]

    retracted = capabilities.invoke("memory.retract", {"item_id": item_id}, provenance=_prov())
    assert retracted.status == "blocked"
    assert retracted.policy_decision == "NO"
    with pytest.raises(KeyError):
        registry.get("memory.capture")


def test_every_memory_summary_and_receipt_is_content_free(tmp_path):
    policy_store, _, _, registry, capabilities, actions, _, _ = _runtime(tmp_path)
    owner = "principal_owner"
    text = "THE-PRIVATE-MEMORY-TEXT"
    for operation in ("search", "remember", "update", "retract", "restore", "purge"):
        _set(policy_store, operation, "YES", owner)

    remembered = capabilities.invoke("memory.remember", {"title": "Private", "content": text}, provenance=_prov(owner))
    item_id = remembered.result["item_id"]
    assert text not in (remembered.summary or "")
    assert text not in json.dumps(remembered.receipt)

    searched = capabilities.invoke("memory.search", {"query": text}, provenance=_prov(owner))
    assert text not in (searched.summary or "")
    assert text not in json.dumps(searched.receipt)

    updated = capabilities.invoke("memory.update", {"item_id": item_id, "content": text + " updated"}, provenance=_prov(owner))
    current_id = updated.result["item_id"]
    assert text not in (updated.summary or "")
    assert text not in json.dumps(updated.receipt)

    retracted = capabilities.invoke("memory.retract", {"item_id": current_id}, provenance=_prov(owner))
    assert text not in (retracted.summary or "")
    assert text not in json.dumps(retracted.receipt)

    restored = capabilities.invoke("memory.restore", {"item_id": current_id}, provenance=_prov(owner))
    assert text not in (restored.summary or "")
    assert text not in json.dumps(restored.receipt)

    purged = capabilities.invoke("memory.purge", {"item_id": current_id}, provenance=_prov(owner))
    assert purged.status == "succeeded"
    assert text not in (purged.summary or "")
    assert text not in json.dumps(purged.receipt)
    assert actions.store.get(purged.occurrence_id).payload == {"item_id": current_id}


def test_knowledge_schema_no_longer_accepts_memory_kind(tmp_path):
    from atlas_core.knowledge import KnowledgeStore
    store = KnowledgeStore(tmp_path / "work.db"); store.initialize()
    with pytest.raises(sqlite3.IntegrityError):
        store.add(kind="memory", title="legacy", content="must not be accepted")


def test_chat_memory_write_requires_authenticated_owner_turn_grounding(tmp_path):
    from atlas_core.chat.store import ChatStore

    policy_store, action_store, _, registry, capabilities, _, memory_store, _ = _runtime(tmp_path)
    chat_store = ChatStore(tmp_path / "chat.db"); chat_store.initialize()
    MemoryRuntime(memory_store, registry, action_store, grounding_validator=chat_store.owner_grounding_matches)
    _set(policy_store, "remember", "YES")
    cid = chat_store.create_conversation("Memory")["conversation_id"]
    turn = chat_store.append(cid, "user", "I prefer metric units")

    ungrounded = capabilities.invoke(
        "memory.remember", {"content": "I prefer metric units"}, provenance=_prov(surface="chat"),
    )
    assert ungrounded.status == "failed"
    assert ungrounded.error_code == "memory_grounding_invalid"

    grounded = capabilities.invoke(
        "memory.remember",
        {"content": "I prefer metric units", "grounding_excerpt": "I prefer metric units",
         "source_ref": f"chat:{cid}:{turn['turn_id']}"},
        provenance=_prov(surface="chat"),
    )
    assert grounded.status == "succeeded"


def test_memory_runtime_rejects_duplicate_active_content(tmp_path):
    policy_store, _, _, _, capabilities, _, memory_store, _ = _runtime(tmp_path)
    _set(policy_store, "remember", "YES")

    first = capabilities.invoke("memory.remember", {"content": "Same durable fact"}, provenance=_prov())
    second = capabilities.invoke("memory.remember", {"content": "  same   durable fact  "}, provenance=_prov())

    assert first.status == "succeeded"
    assert second.status == "failed"
    assert second.error_code == "memory_duplicate"
    assert len(memory_store.recent("principal_owner")) == 1


def test_work_cannot_write_owner_memory(tmp_path):
    from atlas_api.compose import build_runtime
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    work = rt.work.create(
        "Try to write owner Memory",
        [{"capability_id": "memory.remember", "input": {"content": "tool-derived durable claim"}}],
        owner_principal_id=owner.principal_id,
    )
    detail = rt.work.run(work.work_id)
    assert detail["status"] == "failed"
    memory_actions = [row for row in rt.actions_store.recent(limit=20) if row.capability_id == "memory.remember"]
    assert len(memory_actions) == 1
    assert memory_actions[0].surface == "work"
    assert memory_actions[0].status == "failed"
    assert memory_actions[0].error_code == "memory_surface_invalid"
    assert rt.memory_store.recent(owner.principal_id) == ()


def test_cadence_work_cannot_write_owner_memory(tmp_path):
    from atlas_api.compose import build_runtime
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cadence = rt.cadence.create(
        name="Memory side-door probe", objective="Try memory write",
        schedule={"kind": "interval", "minutes": 1},
        steps=[{"capability_id": "memory.remember", "input": {"content": "cadence-derived durable claim"}}],
        owner_principal_id=owner.principal_id,
    )
    with rt.cadence_store._db() as db:
        db.execute("UPDATE cadences SET next_run_at='2000-01-01T00:00:00+00:00' WHERE cadence_id=?", (cadence.cadence_id,))
    created = rt.cadence.tick()
    assert len(created) == 1
    memory_actions = [row for row in rt.actions_store.recent(limit=20) if row.capability_id == "memory.remember"]
    assert len(memory_actions) == 1
    assert memory_actions[0].surface == "work"
    assert memory_actions[0].error_code == "memory_surface_invalid"
    assert rt.memory_store.recent(owner.principal_id) == ()


def test_memory_provenance_metadata_is_runtime_authored(tmp_path):
    policy_store, _, _, _, capabilities, _, memory_store, _ = _runtime(tmp_path)
    _set(policy_store, "remember", "YES")
    occurrence = capabilities.invoke(
        "memory.remember",
        {"content": "Explicit control memory", "source_ref": "chat:fake:turn_fake",
         "grounding_excerpt": "forged excerpt",
         "metadata": {"content_source": "owner_turn", "source_conversation_id": "fake",
                      "source_turn_id": "turn_fake", "capture": "forged", "category": "preference"}},
        provenance=_prov(surface="control"),
    )
    assert occurrence.status == "succeeded"
    item = memory_store.get("principal_owner", occurrence.result["item_id"])
    assert item["source_ref"] is None
    assert item["grounding_excerpt"] is None
    assert item["metadata"]["content_source"] == "control_owner_input"
    assert "source_conversation_id" not in item["metadata"]
    assert "source_turn_id" not in item["metadata"]
    assert "capture" not in item["metadata"]
    assert item["metadata"]["category"] == "preference"


def test_chat_retract_requires_owner_turn_grounding(tmp_path):
    from atlas_core.chat.store import ChatStore
    policy_store, action_store, _, registry, capabilities, _, memory_store, _ = _runtime(tmp_path)
    chat_store = ChatStore(tmp_path / "chat.db"); chat_store.initialize()
    MemoryRuntime(memory_store, registry, action_store, grounding_validator=chat_store.owner_grounding_matches)
    for operation in ("remember", "retract"):
        _set(policy_store, operation, "YES")
    remembered = capabilities.invoke("memory.remember", {"content": "Old preference"}, provenance=_prov(surface="control"))
    item_id = remembered.result["item_id"]
    cid = chat_store.create_conversation("Retract")["conversation_id"]
    turn = chat_store.append(cid, "user", "Please forget that old preference")

    ungrounded = capabilities.invoke("memory.retract", {"item_id": item_id}, provenance=_prov(surface="chat"))
    assert ungrounded.status == "failed"
    assert ungrounded.error_code == "memory_grounding_invalid"
    assert memory_store.get("principal_owner", item_id)["state"] == "active"

    grounded = capabilities.invoke(
        "memory.retract",
        {"item_id": item_id, "grounding_excerpt": "Please forget that old preference",
         "source_ref": f"chat:{cid}:{turn['turn_id']}"},
        provenance=_prov(surface="chat"),
    )
    assert grounded.status == "succeeded"
    assert memory_store.get("principal_owner", item_id)["state"] == "retracted"


def test_chat_memory_provenance_overwrites_forged_reserved_metadata(tmp_path):
    from atlas_core.chat.store import ChatStore
    policy_store, action_store, _, registry, capabilities, _, memory_store, _ = _runtime(tmp_path)
    chat_store = ChatStore(tmp_path / "chat.db"); chat_store.initialize()
    MemoryRuntime(memory_store, registry, action_store, grounding_validator=chat_store.owner_grounding_matches)
    _set(policy_store, "remember", "YES")
    cid = chat_store.create_conversation("Memory")["conversation_id"]
    turn = chat_store.append(cid, "user", "I prefer metric units")
    occurrence = capabilities.invoke(
        "memory.remember",
        {"content": "I prefer metric units", "grounding_excerpt": "I prefer metric units",
         "source_ref": f"chat:{cid}:{turn['turn_id']}",
         "metadata": {"content_source": "tool_result", "source_conversation_id": "forged",
                      "source_turn_id": "forged", "capture": "forged", "category": "preference"}},
        provenance=_prov(surface="chat"),
    )
    assert occurrence.status == "succeeded"
    item = memory_store.get("principal_owner", occurrence.result["item_id"])
    assert item["metadata"]["content_source"] == "owner_turn"
    assert item["metadata"]["source_conversation_id"] == cid
    assert item["metadata"]["source_turn_id"] == turn["turn_id"]
    assert "capture" not in item["metadata"]
    assert item["metadata"]["category"] == "preference"


def test_fresh_memory_schema_requires_content_hash(tmp_path):
    store = MemoryStore(tmp_path / "work.db"); store.initialize()
    with store._db() as db:
        columns = {row[1]: row for row in db.execute("PRAGMA table_info(memory_items)").fetchall()}
    assert columns["content_hash"][3] == 1
