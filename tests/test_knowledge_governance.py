from __future__ import annotations

from starlette.testclient import TestClient

from atlas_api.app import create_app
from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.evidence import EvidenceStore
from atlas_core.identity import IdentityStore
from atlas_core.knowledge import KnowledgeRuntime, KnowledgeStore
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.provenance import InvocationProvenance


def _runtime(tmp_path):
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
    store = KnowledgeStore(work_db); store.initialize()
    knowledge = KnowledgeRuntime(store, registry)
    return owner, policy_store, action_store, actions, capabilities, store, knowledge, registry


def _seed(policy_store, owner, operation, decision):
    policy_store.set(principal_id=owner.principal_id, scope="atlas/knowledge", operation=operation, decision=decision)


def _invoke(capabilities, owner, cid, payload):
    return capabilities.invoke(cid, payload, provenance=InvocationProvenance(owner.principal_id, "human", "control"))


def test_promote_is_the_only_curated_write_path_and_requires_provenance(tmp_path):
    owner, policy_store, _store_actions, _actions, capabilities, store, _knowledge, registry = _runtime(tmp_path)
    _seed(policy_store, owner, "promote", "YES")

    occurrence = _invoke(capabilities, owner, "knowledge.promote", {
        "title": "Torque spec", "content": "Bolt torque is 42 Nm.", "source_ref": "artifact:artifact_abc",
    })
    assert occurrence.status == "succeeded"
    item = store.get(occurrence.result["item_id"])
    assert item["source_ref"] == "artifact:artifact_abc"
    assert item["kind"] == "reference"

    # A blank source_ref is refused: curated knowledge always carries provenance.
    blank = _invoke(capabilities, owner, "knowledge.promote", {
        "title": "Ungrounded", "content": "No provenance.", "source_ref": "   ",
    })
    assert blank.status == "failed"
    assert blank.error_code == "knowledge_source_ref_required"

    # The write-orphan is closed only through promote: no generic add capability exists.
    assert "knowledge.add" not in {item.definition.id for item in registry.all()}


def test_curated_delete_uses_live_no_yes_policy(tmp_path):
    owner, policy_store, action_store, _actions, capabilities, store, _knowledge, _registry = _runtime(tmp_path)
    _seed(policy_store, owner, "promote", "YES")
    _seed(policy_store, owner, "delete", "YES")
    item_id = _invoke(capabilities, owner, "knowledge.promote", {
        "title": "Torque spec", "content": "Bolt torque is 42 Nm.", "source_ref": "artifact:artifact_abc",
    }).result["item_id"]

    deleted = _invoke(capabilities, owner, "knowledge.delete", {"item_id": item_id})
    assert deleted.status == "succeeded"
    assert "42 Nm" not in (deleted.summary or "")
    try:
        store.get(item_id)
        raise AssertionError("curated item should be gone")
    except KeyError:
        pass

    second = _invoke(capabilities, owner, "knowledge.promote", {
        "title": "Second", "content": "Another fact.", "source_ref": "artifact:artifact_def",
    }).result["item_id"]
    _seed(policy_store, owner, "delete", "NO")
    blocked = _invoke(capabilities, owner, "knowledge.delete", {"item_id": second})
    assert blocked.status == "blocked"
    assert store.get(second)["item_id"] == second
    assert action_store.get(blocked.occurrence_id).status == "blocked"


def test_retrieve_returns_the_grounded_contract_shape_with_raw_bm25_order(tmp_path):
    owner, policy_store, _action_store, _actions, capabilities, store, knowledge, _registry = _runtime(tmp_path)
    _seed(policy_store, owner, "retrieve", "YES")
    store.add(kind="reference", title="Hydraulic pump", content="The hydraulic pump service interval is 500 hours.", source_ref="artifact:pump")
    store.add(kind="note", title="Unrelated", content="Coffee machine descaling notes for the kitchen.", source_ref="chat:c:t")

    occurrence = _invoke(capabilities, owner, "knowledge.retrieve", {"need": "hydraulic pump service interval"})
    assert occurrence.status == "succeeded"
    rows = occurrence.result
    assert rows, "expected a curated hit"
    first = rows[0]
    assert set(first) == {"content", "score", "mechanism", "grounding"}
    assert first["mechanism"] == "fts.bm25@curated"
    assert first["grounding"]["tier"] == "curated"
    assert first["grounding"]["source_ref"] == "artifact:pump"
    assert "hydraulic pump" in first["content"].casefold()

    # Raw bm25: lower is better, and the better match must sort first.
    scored = knowledge.retrieve("hydraulic pump service interval descaling", limit=10)
    assert len(scored) >= 2
    assert scored[0]["score"] < scored[-1]["score"]
    assert "hydraulic" in scored[0]["content"].casefold()


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_COMPANION_PASSWORD", "secret")
    monkeypatch.setenv("ATLAS_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("ATLAS_ENV", "development")
    return TestClient(create_app(instance_root=tmp_path / "instance", static_dir=tmp_path / "missing"))


def test_http_knowledge_mutations_cross_the_capability_gate(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        csrf = client.post("/api/auth/login", json={"password": "secret"}).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}

        created = client.post("/api/knowledge/promote", headers=headers, json={
            "title": "Torque spec", "content": "Bolt torque is 42 Nm.", "source_ref": "artifact:artifact_abc",
        })
        assert created.status_code == 201
        assert created.json()["action"]["status"] == "succeeded"
        item_id = created.json()["item"]["item_id"]

        listed = client.get("/api/knowledge")
        assert item_id in {row["item_id"] for row in listed.json()["items"]}

        removed = client.delete(f"/api/knowledge/{item_id}", headers=headers)
        assert removed.status_code == 200
        body = removed.json()
        assert body["action"]["status"] == "succeeded"
        assert body["action"]["operation"] == "delete"
        assert client.get("/api/knowledge").json()["items"] == []
