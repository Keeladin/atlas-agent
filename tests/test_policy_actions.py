from __future__ import annotations

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

    ordinary = policy.resolve(principal_id=owner, scope="files/local/root/manual.pdf", operation="read")
    secret = policy.resolve(principal_id=owner, scope="files/local/root/secrets/key", operation="read")
    assert ordinary.decision == "YES"
    assert ordinary.matched_operation == "*"
    assert secret.decision == "NO"
    assert secret.matched_scope == "files/local/root/secrets"


def test_yes_executes_no_blocks_and_confirm_is_not_a_policy_value(tmp_path):
    policy_store, action_store, registry, capabilities, _actions = _runtime(tmp_path)
    calls: list[dict] = []
    _register_echo(registry, calls)
    owner = "principal_owner"
    provenance = InvocationProvenance(owner, "human", "chat")

    blocked = capabilities.invoke("test.echo", {"value": "no"}, provenance=provenance)
    assert blocked.status == "blocked"
    assert blocked.policy_decision == "NO"
    assert calls == []

    policy_store.set(principal_id=owner, scope="thing/item", operation="run", decision="YES")
    allowed = capabilities.invoke("test.echo", {"value": "yes"}, provenance=provenance)
    assert allowed.status == "succeeded"
    assert calls == [{"value": "yes"}]
    assert len(allowed.payload_sha256) == 64
    assert action_store.get(allowed.occurrence_id).payload_sha256 == allowed.payload_sha256

    with pytest.raises(ValueError, match="unsupported policy decision"):
        policy_store.set(principal_id=owner, scope="thing/item", operation="run", decision="CONFIRM")  # type: ignore[arg-type]


def test_current_no_blocks_a_new_invocation_after_prior_yes(tmp_path):
    policy_store, _, registry, capabilities, _actions = _runtime(tmp_path)
    calls: list[dict] = []
    _register_echo(registry, calls)
    owner = "principal_owner"
    provenance = InvocationProvenance(owner, "human", "chat")

    policy_store.set(principal_id=owner, scope="thing/item", operation="run", decision="YES")
    assert capabilities.invoke("test.echo", {"value": "first"}, provenance=provenance).status == "succeeded"
    policy_store.set(principal_id=owner, scope="thing/item", operation="run", decision="NO")
    blocked = capabilities.invoke("test.echo", {"value": "later"}, provenance=provenance)
    assert blocked.status == "blocked"
    assert calls == [{"value": "first"}]
