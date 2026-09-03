from __future__ import annotations

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, ScopeResolution
from atlas_api.compose import build_runtime


def test_work_rechecks_current_policy_and_resumes_after_owner_grants_domain(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    calls: list[dict] = []

    def resolve(payload):
        return ScopeResolution("external/device/pump-1", dict(payload), "Start pump 1")

    def execute(payload):
        calls.append(dict(payload))
        return ActionResult(True, {"started": True}, {"ok": True})

    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition("test.pump.start", "Start a test pump.", "start", "external", {"type": "object", "properties": {}, "additionalProperties": False}),
        resolve, execute, metadata={"scope_hint": "external/device/pump-1"},
    ))
    rt.policy_store.set(principal_id=owner, scope="external/device/pump-1", operation="start", decision="NO")
    work = rt.work.create("Start the test pump", [{"capability_id": "test.pump.start", "input": {}}], owner_principal_id=owner)

    blocked = rt.work.run(work.work_id)
    assert blocked["status"] == "paused"
    assert blocked["steps"][0]["status"] == "waiting"
    assert rt.actions_store.get(blocked["steps"][0]["occurrence_id"]).status == "blocked"
    assert calls == []

    rt.policy_store.set(principal_id=owner, scope="external/device/pump-1", operation="start", decision="YES")
    resumed = rt.work.resume(work.work_id)
    assert resumed["status"] == "completed"
    assert resumed["steps"][0]["status"] == "completed"
    assert calls == [{}]


def test_failed_work_resume_retries_from_failed_step_without_replaying_completed_steps(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    calls = {"first": 0, "flaky": 0}

    def register(cid, op, execute):
        rt.capabilities_registry.register(CapabilityRegistration(
            CapabilityDefinition(cid, cid, op, "internal", {"type": "object", "properties": {}, "additionalProperties": False}),
            lambda payload, _op=op: ScopeResolution("test/retry", dict(payload), _op),
            execute,
        ))
        rt.policy_store.set(principal_id=owner, scope="test/retry", operation=op, decision="YES")

    def first(payload):
        calls["first"] += 1
        return ActionResult(True, {"first": True}, {"ok": True})

    def flaky(payload):
        calls["flaky"] += 1
        if calls["flaky"] == 1:
            return ActionResult(False, {}, {"ok": False}, error_code="transient", error="try again")
        return ActionResult(True, {"second": True}, {"ok": True})

    register("test.first", "first", first)
    register("test.flaky", "flaky", flaky)
    work = rt.work.create(
        "Retry a transient failure",
        [{"capability_id": "test.first", "input": {}}, {"capability_id": "test.flaky", "input": {}}],
        owner_principal_id=owner,
    )
    failed = rt.work.run(work.work_id)
    assert failed["status"] == "failed"
    assert [step["status"] for step in failed["steps"]] == ["completed", "failed"]

    resumed = rt.work.resume(work.work_id)
    assert resumed["status"] == "completed"
    assert [step["status"] for step in resumed["steps"]] == ["completed", "completed"]
    assert calls == {"first": 1, "flaky": 2}


def test_concurrent_runs_claim_step_once(tmp_path):
    import threading
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    entered = threading.Event(); release = threading.Event(); calls = []

    def resolve(payload):
        return ScopeResolution("external/test/once", dict(payload), "Run once")

    def execute(payload):
        calls.append(dict(payload)); entered.set()
        assert release.wait(2)
        return ActionResult(True, {"done": True}, {"ok": True})

    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition("test.once", "Run once.", "run", "internal",
                             {"type": "object", "properties": {}, "additionalProperties": False}),
        resolve, execute, metadata={"scope_hint": "external/test/once"},
    ))
    rt.policy_store.set(principal_id=owner, scope="external/test/once", operation="run", decision="YES")
    work = rt.work.create("Only once", [{"capability_id": "test.once", "input": {}}], owner_principal_id=owner)
    results = []
    first = threading.Thread(target=lambda: results.append(rt.work.run(work.work_id)))
    first.start(); assert entered.wait(2)
    second = threading.Thread(target=lambda: results.append(rt.work.run(work.work_id)))
    second.start(); second.join(2)
    release.set(); first.join(2)
    assert len(calls) == 1
    assert rt.work.detail(work.work_id)["status"] == "completed"
