from __future__ import annotations

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, ScopeResolution
from atlas_api.compose import build_runtime


def test_work_waits_for_confirm_then_current_no_wins(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    calls: list[dict] = []

    def resolve(payload):
        return ScopeResolution("external/device/pump-1", dict(payload), "Start pump 1")

    def execute(payload):
        calls.append(dict(payload))
        return ActionResult(True, {"started": True}, {"ok": True})

    rt.capabilities_registry.register(
        CapabilityRegistration(
            CapabilityDefinition(
                "test.pump.start", "Start a test pump.", "start", "external",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            resolve,
            execute,
            metadata={"scope_hint": "external/device/pump-1"},
        )
    )
    rt.policy_store.set(
        principal_id=owner,
        scope="external/device/pump-1",
        operation="start",
        decision="CONFIRM",
    )
    work = rt.work.create(
        "Start the test pump",
        [{"capability_id": "test.pump.start", "input": {}}],
        owner_principal_id=owner,
    )
    waiting = rt.work.run(work.work_id)
    assert waiting["status"] == "waiting_confirmation"
    step = waiting["steps"][0]
    pending = rt.actions_store.get(step["occurrence_id"])
    assert pending.status == "pending_confirmation"
    assert calls == []

    rt.policy_store.set(
        principal_id=owner,
        scope="external/device/pump-1",
        operation="start",
        decision="NO",
    )
    blocked = rt.actions.confirm(pending.occurrence_id, principal_id=owner)
    assert blocked.status == "blocked"
    assert blocked.error_code == "policy_revoked_before_execution"

    final = rt.work.run(work.work_id)
    assert final["status"] == "failed"
    assert final["steps"][0]["status"] == "failed"
    assert calls == []
