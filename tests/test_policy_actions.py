from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from atlas_core.actions import ActionRequest, ActionResult, ActionRuntime, ActionStore
from atlas_core.capabilities import (
    CapabilityDefinition,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilityRuntime,
    ScopeResolution,
)
from atlas_core.evidence import EvidenceStore
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.provenance import InvocationProvenance


def _runtime(tmp_path):
    identity_db = tmp_path / "identity.db"
    work_db = tmp_path / "work.db"
    policy_store = PolicyStore(identity_db)
    policy_store.initialize()
    action_store = ActionStore(work_db)
    action_store.initialize()
    evidence = EvidenceStore(work_db)
    evidence.initialize()
    registry = CapabilityRegistry()
    policy = OwnerPolicy(policy_store)
    actions = ActionRuntime(
        policy=policy,
        store=action_store,
        evidence=evidence,
        executor_resolver=registry.executor,
    )
    capabilities = CapabilityRuntime(registry, actions, policy)
    return policy_store, action_store, registry, capabilities, actions


def _register_echo(registry, calls):
    def resolve(payload):
        return ScopeResolution("thing/item", dict(payload), "Do the thing")

    def execute(payload):
        calls.append(dict(payload))
        return ActionResult(True, {"echo": payload}, {"ok": True})

    registry.register(
        CapabilityRegistration(
            CapabilityDefinition(
                "test.echo", "Echo one governed payload.", "run", "external",
                {"type": "object", "properties": {"value": {"type": "string"}}, "additionalProperties": False},
            ),
            resolve,
            execute,
            metadata={"scope_hint": "thing/item"},
        )
    )


def test_policy_defaults_no_and_more_specific_scope_wins(tmp_path):
    policy_store, _, _, _, _ = _runtime(tmp_path)
    owner = "principal_owner"
    policy = OwnerPolicy(policy_store)

    assert policy.resolve(principal_id=owner, scope="files/local/root/a.txt", operation="read").decision == "NO"
    policy_store.set(principal_id=owner, scope="files/local/root", operation="*", decision="YES")
    policy_store.set(principal_id=owner, scope="files/local/root/secrets", operation="*", decision="NO")
    policy_store.set(principal_id=owner, scope="files/local/root", operation="read", decision="CONFIRM")

    ordinary = policy.resolve(principal_id=owner, scope="files/local/root/manual.pdf", operation="read")
    secret = policy.resolve(principal_id=owner, scope="files/local/root/secrets/key", operation="read")
    assert ordinary.decision == "CONFIRM"
    assert ordinary.matched_operation == "read"
    assert secret.decision == "NO"
    assert secret.matched_scope == "files/local/root/secrets"


def test_yes_executes_no_blocks_and_confirm_is_exact(tmp_path):
    policy_store, action_store, registry, capabilities, actions = _runtime(tmp_path)
    calls: list[dict] = []
    _register_echo(registry, calls)
    owner = "principal_owner"
    provenance = InvocationProvenance(owner, "human", "chat")

    blocked = capabilities.invoke("test.echo", {"value": "no"}, provenance=provenance)
    assert blocked.status == "blocked"
    assert calls == []

    policy_store.set(principal_id=owner, scope="thing/item", operation="run", decision="YES")
    allowed = capabilities.invoke("test.echo", {"value": "yes"}, provenance=provenance)
    assert allowed.status == "succeeded"
    assert calls == [{"value": "yes"}]

    policy_store.set(principal_id=owner, scope="thing/item", operation="run", decision="CONFIRM")
    pending = capabilities.invoke("test.echo", {"value": "confirm"}, provenance=provenance)
    assert pending.status == "pending_confirmation"
    assert pending.payload == {"value": "confirm"}
    assert len(pending.payload_sha256) == 64
    assert calls == [{"value": "yes"}]

    with pytest.raises(PermissionError):
        actions.confirm(pending.occurrence_id, principal_id="principal_other")

    confirmed = actions.confirm(pending.occurrence_id, principal_id=owner)
    assert confirmed.status == "succeeded"
    assert calls[-1] == {"value": "confirm"}
    assert action_store.get(pending.occurrence_id).payload_sha256 == pending.payload_sha256


def test_confirmation_rechecks_current_policy_before_execution(tmp_path):
    policy_store, _, registry, capabilities, actions = _runtime(tmp_path)
    calls: list[dict] = []
    _register_echo(registry, calls)
    owner = "principal_owner"
    provenance = InvocationProvenance(owner, "human", "chat")

    policy_store.set(principal_id=owner, scope="thing/item", operation="run", decision="CONFIRM")
    pending = capabilities.invoke("test.echo", {"value": "later"}, provenance=provenance)
    assert pending.status == "pending_confirmation"

    policy_store.set(principal_id=owner, scope="thing/item", operation="run", decision="NO")
    result = actions.confirm(pending.occurrence_id, principal_id=owner)
    assert result.status == "blocked"
    assert result.error_code == "policy_revoked_before_execution"
    assert calls == []
