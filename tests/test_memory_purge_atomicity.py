from __future__ import annotations

import json

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
    registry = CapabilityRegistry(); policy = OwnerPolicy(policy_store)
    actions = ActionRuntime(policy=policy, store=action_store, evidence=evidence, executor_resolver=registry.executor)
    capabilities = CapabilityRuntime(registry, actions, policy)
    memory_store = MemoryStore(work_db); memory_store.initialize()
    memory = MemoryRuntime(memory_store, registry, action_store)
    return policy_store, action_store, evidence, capabilities, memory_store, memory


def _prov(owner: str = "principal_owner", surface: str = "control") -> InvocationProvenance:
    return InvocationProvenance(owner, "human", surface)


def _set(policy_store, operation: str, decision: str, owner: str = "principal_owner") -> None:
    policy_store.set(principal_id=owner, scope="atlas/memory", operation=operation, decision=decision)


def test_purge_rolls_back_memory_delete_and_redaction_together(tmp_path, monkeypatch):
    import atlas_core.memory.runtime as memory_runtime_module
    policy_store, action_store, _, capabilities, memory_store, _ = _runtime(tmp_path)
    _set(policy_store, "remember", "YES"); _set(policy_store, "purge", "YES")
    text = "atomic secret text"
    remembered = capabilities.invoke("memory.remember", {"content": text}, provenance=_prov())
    item_id = remembered.result["item_id"]
    before = action_store.get(remembered.occurrence_id)
    before_index = memory_store.index_status()
    assert before_index["vectors"] == 1
    assert before_index["representations"] == 1
    original = memory_runtime_module._redact_occurrences

    def explode(db, actions_store, **kwargs):
        original(db, actions_store, **kwargs)
        raise RuntimeError("forced redaction failure")

    monkeypatch.setattr(memory_runtime_module, "_redact_occurrences", explode)
    purged = capabilities.invoke("memory.purge", {"item_id": item_id}, provenance=_prov())
    assert purged.status == "failed"
    assert memory_store.get("principal_owner", item_id)["content"] == text
    after = action_store.get(remembered.occurrence_id)
    assert after.payload == before.payload
    assert after.payload_sha256 == before.payload_sha256
    after_index = memory_store.index_status()
    assert after_index["vectors"] == 1
    assert after_index["representations"] == 1


def test_hash_matching_reaches_blocked_occurrence_without_item_id(tmp_path):
    policy_store, action_store, _, capabilities, _, _ = _runtime(tmp_path)
    text = "blocked memory content that must disappear"
    blocked = capabilities.invoke("memory.remember", {"content": text}, provenance=_prov())
    assert blocked.status == "blocked"
    assert "item_id" not in blocked.payload

    _set(policy_store, "remember", "YES")
    remembered = capabilities.invoke("memory.remember", {"content": text}, provenance=_prov())
    item_id = remembered.result["item_id"]
    _set(policy_store, "purge", "YES")
    purged = capabilities.invoke("memory.purge", {"item_id": item_id}, provenance=_prov())
    assert purged.status == "succeeded"

    redacted = action_store.get(blocked.occurrence_id)
    assert text not in json.dumps(redacted.payload)
    assert redacted.payload.get("__redacted") is True
    assert redacted.payload_sha256 == blocked.payload_sha256


def test_pending_confirmation_window_is_untouchable_and_hashes_stay_attested(tmp_path):
    policy_store, action_store, _, capabilities, _, _ = _runtime(tmp_path)
    text = "confirmation-window-secret"
    _set(policy_store, "remember", "YES")
    remembered = capabilities.invoke("memory.remember", {"content": text}, provenance=_prov())
    item_id = remembered.result["item_id"]

    _set(policy_store, "remember", "CONFIRM")
    pending = capabilities.invoke("memory.remember", {"content": text}, provenance=_prov())
    pending_hash = pending.payload_sha256
    assert pending.status == "pending_confirmation"

    _set(policy_store, "purge", "YES")
    capabilities.invoke("memory.purge", {"item_id": item_id}, provenance=_prov())

    still_pending = action_store.get(pending.occurrence_id)
    assert still_pending.status == "pending_confirmation"
    assert still_pending.payload["content"] == text
    assert still_pending.payload_sha256 == pending_hash
    redacted = action_store.get(remembered.occurrence_id)
    assert redacted.payload_sha256 == remembered.payload_sha256
    assert text not in json.dumps(redacted.payload)


def test_three_deep_cycle_safe_chain_purges_all_rows_and_fts(tmp_path):
    policy_store, action_store, _, capabilities, memory_store, _ = _runtime(tmp_path)
    owner = "principal_owner"
    first = memory_store.add(principal_id=owner, title="one", content="chain content one")
    second = memory_store.update(principal_id=owner, item_id=first["item_id"], title="two", content="chain content two")
    third = memory_store.update(principal_id=owner, item_id=second["item_id"], title="three", content="chain content three")
    with memory_store._db() as db:
        first_pk = db.execute("SELECT memory_pk FROM memory_v2_items WHERE item_id=?", (first["item_id"],)).fetchone()[0]
        third_pk = db.execute("SELECT memory_pk FROM memory_v2_items WHERE item_id=?", (third["item_id"],)).fetchone()[0]
        db.execute("UPDATE memory_v2_items SET supersedes_pk=? WHERE memory_pk=?", (third_pk, first_pk))

    _set(policy_store, "purge", "YES", owner)
    purged = capabilities.invoke("memory.purge", {"item_id": second["item_id"]}, provenance=_prov(owner))
    assert purged.status == "succeeded"
    assert purged.result["purged_items"] == 3
    with memory_store._db() as db:
        assert db.execute("SELECT COUNT(*) FROM memory_v2_items").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_v2_fts").fetchone()[0] == 0
    status = memory_store.index_status()
    assert status["vectors"] == 0
    assert status["representations"] == 0


def test_purge_scrubs_matching_evidence_and_adds_content_free_redaction_evidence(tmp_path):
    policy_store, action_store, evidence, capabilities, _, _ = _runtime(tmp_path)
    _set(policy_store, "remember", "YES"); _set(policy_store, "purge", "YES")
    text = "evidence-memory-secret"
    remembered = capabilities.invoke("memory.remember", {"content": text}, provenance=_prov())
    item_id = remembered.result["item_id"]
    seeded = evidence.add(remembered.occurrence_id, "memory_probe", {"content": text, "item_id": item_id})

    purged = capabilities.invoke("memory.purge", {"item_id": item_id}, provenance=_prov())
    assert purged.status == "succeeded"
    rows = evidence.for_occurrence(remembered.occurrence_id)
    probe = next(row for row in rows if row.evidence_id == seeded.evidence_id)
    assert text not in json.dumps(probe.payload)
    assert probe.payload["content"] == "[purged]"
    assert probe.payload["__redacted"] is True
    redaction = next(row for row in rows if row.kind == "memory_purge_redaction")
    encoded = json.dumps(redaction.payload)
    assert text not in encoded
    assert redaction.payload["occurrence_id"] == remembered.occurrence_id


def test_purge_does_not_redact_its_own_occurrence(tmp_path):
    policy_store, action_store, _, capabilities, _, _ = _runtime(tmp_path)
    _set(policy_store, "remember", "YES"); _set(policy_store, "purge", "YES")
    remembered = capabilities.invoke("memory.remember", {"content": "self scrub guard"}, provenance=_prov())
    item_id = remembered.result["item_id"]
    purged = capabilities.invoke("memory.purge", {"item_id": item_id}, provenance=_prov())
    stored = action_store.get(purged.occurrence_id)
    assert stored.status == "succeeded"
    assert stored.payload.get("__redacted") is None
    assert stored.payload["item_id"] == item_id


def test_memory_runtime_rejects_non_colocated_action_store(tmp_path):
    registry = CapabilityRegistry()
    memory_store = MemoryStore(tmp_path / "memory.db"); memory_store.initialize()
    action_store = ActionStore(tmp_path / "actions.db"); action_store.initialize()
    with pytest.raises(ValueError, match="same SQLite database"):
        MemoryRuntime(memory_store, registry, action_store)


def test_purge_preserves_unrelated_memory_content_in_shared_search_occurrence(tmp_path):
    policy_store, action_store, _, capabilities, _, _ = _runtime(tmp_path)
    for operation in ("remember", "search", "purge"):
        _set(policy_store, operation, "YES")
    first_text = "shared topic private first"
    second_text = "shared topic public second"
    first = capabilities.invoke("memory.remember", {"content": first_text}, provenance=_prov())
    second = capabilities.invoke("memory.remember", {"content": second_text}, provenance=_prov())
    searched = capabilities.invoke("memory.search", {"query": "shared topic"}, provenance=_prov())
    assert searched.status == "succeeded"
    assert any(row["item_id"] == first.result["item_id"] for row in searched.result)
    assert any(row["item_id"] == second.result["item_id"] for row in searched.result)

    purged = capabilities.invoke("memory.purge", {"item_id": first.result["item_id"]}, provenance=_prov())
    assert purged.status == "succeeded"
    redacted_search = action_store.get(searched.occurrence_id)
    assert isinstance(redacted_search.result, list)
    encoded = json.dumps(redacted_search.result)
    assert first_text not in encoded
    assert second_text in encoded
