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


def _prov(owner: str = "principal_owner") -> InvocationProvenance:
    return InvocationProvenance(owner, "human", "chat")


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
