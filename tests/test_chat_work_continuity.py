from __future__ import annotations

import json
import subprocess

import pytest

import atlas_core.host as host_module
from atlas_api.compose import build_runtime
from atlas_core.actions import ActionResult
from atlas_core.capabilities import (
    CapabilityDefinition, CapabilityRegistration, RuntimeContinuityRequired, ScopeResolution,
)
from atlas_core.provenance import InvocationProvenance
from atlas_core.providers import ModelResponse, ProviderRuntime


class SequenceProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ModelResponse(text=self.responses.pop(0), provider_key="test", model="test-model", raw={})


def _completed(args, stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)


def _fake_systemd(args, timeout=20):
    if "show" in args:
        return _completed(args, "Id=atlas-api.service\nLoadState=loaded\nActiveState=active\nSubState=running\nMainPID=222\nInvocationID=new-invocation\nExecMainStatus=0\n")
    return _completed(args)


def _commit_test_obligation(rt, owner_turn):
    attempts = rt.obligation_store.begin_attempt(owner_turn["turn_id"])
    result = rt.obligation_store.commit_intake(
        owner_turn["turn_id"],
        [{"grounding_excerpt": owner_turn["content"], "text": owner_turn["content"], "kind": "state_change"}],
        attempts=attempts, provider="test", model="test",
    )
    return result.obligation_ids[0]


def test_self_restart_requires_work_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")
    monkeypatch.setattr(host_module, "_run", _fake_systemd)
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id

    with pytest.raises(RuntimeContinuityRequired):
        rt.capabilities.invoke(
            "host.service.restart", {"unit": "atlas-api.service"},
            provenance=InvocationProvenance(owner, "human", "chat"),
        )

    assert not [row for row in rt.actions_store.recent(limit=20) if row.capability_id == "host.service.restart"]
    assert rt.work_store.list() == ()


def test_other_service_restart_remains_direct_chat_action(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")
    monkeypatch.setattr(host_module, "_run", _fake_systemd)
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    occurrence = rt.capabilities.invoke(
        "host.service.restart", {"unit": "demo.service"},
        provenance=InvocationProvenance(owner, "human", "chat"),
    )
    assert occurrence.status == "succeeded"
    assert rt.work_store.list() == ()


def test_chat_promotes_self_restart_to_detached_work_after_handoff(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")
    monkeypatch.setattr(host_module, "_run", _fake_systemd)
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Restart")['conversation_id']
    rt.chat.provider = SequenceProvider(
        '{"kind":"capability","capability_id":"host.service.restart","input":{"unit":"atlas-api.service"}}',
        json.dumps({"kind":"capability","capability_id":"work.create","input":{
            "objective":"Restart the Atlas API and verify it is running",
            "steps":[
                {"capability_id":"host.service.restart","description":"Restart Atlas API","input":{"unit":"atlas-api.service"}},
                {"capability_id":"host.service.status","description":"Verify Atlas API service state","input":{"unit":"atlas-api.service"}}
            ]
        }}),
        '{"kind":"reply","reply":"I moved that into durable Work before restarting the runtime."}',
    )
    result = rt.chat.send(cid, "Restart your API and verify it comes back healthy", principal_id=owner.principal_id, defer_capture=True)
    assert result["turn"]["content"].startswith("I moved that into durable Work")
    assert not [row for row in rt.actions_store.recent(limit=30) if row.capability_id == "host.service.restart"]
    work = rt.work_store.list(limit=10)[0]
    assert work.status == "staged"
    assert work.metadata["chat_origin"]["conversation_id"] == cid
    owner_turn = rt.chat_store.turn(result["_owner_turn_id"])
    assert owner_turn["response_handed_off_at"] is None
    rt.chat_store.mark_response_handed_off(owner_turn["turn_id"])
    assert rt.work.promote_runnable() == (work.work_id,)
    detail = rt.work.run_runnable()[0]
    rows = [row for row in rt.actions_store.recent(limit=30) if row.capability_id == "host.service.restart"]
    assert len(rows) == 1 and rows[0].work_id == work.work_id
    assert rows[0].status == "uncertain"
    assert detail["status"] == "waiting"
    prompt = json.loads(rt.chat.provider.requests[1].input)
    signal = prompt["tool_results"][0]
    assert signal["status"] == "durable_required"


def test_uncertain_direct_action_is_adopted_not_replayed(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    calls = []
    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition("test.async.start", "Start async test operation", "start", "external",
                             {"type":"object","properties":{"target":{"type":"string"}},"required":["target"],"additionalProperties":False}),
        lambda payload: ScopeResolution("test/async/" + payload["target"], dict(payload), "Start async operation"),
        lambda payload: (calls.append(dict(payload)) or ActionResult(True, {"accepted": True}, {"ok": True, "verification_pending": True})),
    ))
    rt.policy_store.set(principal_id=owner.principal_id, scope="test/async/job-1", operation="start", decision="YES")
    cid = rt.chat_store.create_conversation("Async")['conversation_id']
    rt.chat.provider = SequenceProvider(
        '{"kind":"capability","capability_id":"test.async.start","input":{"target":"job-1"}}',
        json.dumps({"kind":"capability","capability_id":"work.create","input":{
            "objective":"Start job 1 and retain responsibility for its unresolved outcome",
            "steps":[{"capability_id":"test.async.start","description":"Existing async start","input":{"target":"job-1"}}]
        }}),
        '{"kind":"reply","reply":"I retained the unresolved operation as durable Work."}',
    )
    rt.chat.send(cid, "Start job 1 and keep track of the outcome", principal_id=owner.principal_id, defer_capture=True)
    assert calls == [{"target": "job-1"}]
    occurrences = [row for row in rt.actions_store.recent(limit=20) if row.capability_id == "test.async.start"]
    assert len(occurrences) == 1
    occurrence = occurrences[0]
    assert occurrence.status == "uncertain" and occurrence.work_id
    detail = rt.work.detail(occurrence.work_id)
    assert detail["status"] == "waiting"
    assert detail["steps"][0]["occurrence_id"] == occurrence.occurrence_id


def test_recovery_reconciles_restart_but_detached_loop_resumes_verification(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")
    monkeypatch.setattr(host_module, "_run", _fake_systemd)
    root = tmp_path / "instance"
    rt = build_runtime(root)
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Restart")['conversation_id']
    message = "Restart your API and verify it comes back healthy"
    owner_turn = rt.chat_store.append_owner(cid, message, principal_id=owner.principal_id)
    attempt = rt.obligation_store.begin_attempt(owner_turn["turn_id"])
    obligation_id = rt.obligation_store.commit_intake(
        owner_turn["turn_id"], [{"grounding_excerpt":message,"text":message,"kind":"state_change"}],
        attempts=attempt, provider="test", model="test",
    ).obligation_ids[0]
    work = rt.work.create(
        "Restart the Atlas API and verify it is running",
        [
            {"capability_id":"host.service.restart","description":"Restart Atlas API","input":{"unit":"atlas-api.service"}},
            {"capability_id":"host.service.status","description":"Verify Atlas API service state","input":{"unit":"atlas-api.service"}},
        ],
        owner_principal_id=owner.principal_id,
        metadata={"auto_resume_on_recovery":True,"chat_origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"]}},
        obligation_ids=[obligation_id], stage=True,
    )
    rt.chat_store.append(cid, "assistant", "The restart is staged for detached execution.")
    rt.chat_store.mark_turn_completed(owner_turn["turn_id"])
    rt.chat_store.mark_response_handed_off(owner_turn["turn_id"])
    assert rt.work.promote_runnable() == (work.work_id,)
    waiting = rt.work.run_runnable()[0]
    assert waiting["status"] == "waiting"
    restart_occurrence = rt.actions_store.get(waiting["steps"][0]["occurrence_id"])
    assert restart_occurrence.receipt["predecessor_invocation_id"] == "old-invocation"

    monkeypatch.setenv("INVOCATION_ID", "new-invocation")
    def provider_must_not_run_during_boot(_self, _request):
        raise AssertionError("model provider was called before build_runtime returned")
    monkeypatch.setattr(ProviderRuntime, "generate", provider_must_not_run_during_boot)
    rt2 = build_runtime(root)
    detail = rt2.work.detail(work.work_id)
    assert detail["status"] == "queued"
    assert [step["status"] for step in detail["steps"]] == ["completed", "queued"]
    assert rt2.work.promote_runnable() == (work.work_id,)
    detail = rt2.work.run_runnable()[0]
    assert detail["status"] == "completed"
    assert [step["status"] for step in detail["steps"]] == ["completed", "completed"]
    assert not [turn for turn in rt2.chat_store.turns(cid) if turn["metadata"].get("work_completion")]
    assert rt2.obligation_reconciler.reconcile_noncommunication() == (obligation_id,)
    assert rt2.obligation_store.get(obligation_id).status == "resolved"


def test_recover_executing_preserves_pre_dispatch_restart_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")
    monkeypatch.setattr(host_module, "_run", _fake_systemd)
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    registration = rt.capabilities_registry.get("host.service.restart")
    resolved = registration.resolve_scope({"unit":"atlas-api.service"})
    from atlas_core.actions import ActionRequest
    request = ActionRequest(
        "host.service.restart", "restart", resolved.scope, resolved.payload,
        InvocationProvenance(owner, "human", "work"), work_id="work-x", step_id="step-x",
        initial_receipt=resolved.pre_execution_receipt,
    )
    occurrence = rt.actions_store.create(request, decision="YES", revision=rt.policy_store.revision(), event_id=None, status="executing")
    assert occurrence.receipt["predecessor_invocation_id"] == "old-invocation"
    rt.actions_store.recover_executing()
    recovered = rt.actions_store.get(occurrence.occurrence_id)
    assert recovered.status == "uncertain"
    assert recovered.receipt["predecessor_invocation_id"] == "old-invocation"
    assert recovered.receipt["recovery_required"] is True


def test_self_restart_rate_limit_prevents_restart_storm(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")
    calls = []
    def fake_run(args, timeout=20):
        calls.append(list(args)); return _fake_systemd(args, timeout)
    monkeypatch.setattr(host_module, "_run", fake_run)
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    provenance = InvocationProvenance(owner, "human", "work")

    first = rt.capabilities.invoke("host.service.restart", {"unit":"atlas-api.service"}, provenance=provenance, work_id="work-1", step_id="step-1")
    second = rt.capabilities.invoke("host.service.restart", {"unit":"atlas-api.service"}, provenance=provenance, work_id="work-2", step_id="step-2")
    assert first.status == "uncertain"
    dispatched_at = str(first.receipt.get("dispatched_at") or "")
    assert "T" in dispatched_at and dispatched_at.endswith("+00:00")
    assert rt.actions_store.has_recent_receipt(
        capability_id="host.service.restart", scope="host/service/atlas-api.service",
        receipt_key="dispatched_at", within_seconds=31.0,
    ) is True
    assert second.status == "failed"
    assert second.error_code == "self_restart_rate_limited"
    restart_calls = [row for row in calls if row[:3] == ["systemctl", "--user", "restart"]]
    assert len(restart_calls) == 1


def test_identical_work_composition_from_same_owner_turn_is_idempotent(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Duplicate")['conversation_id']
    owner_turn = rt.chat_store.append_owner(cid, "Do the durable thing", principal_id=owner)
    obligation_id = _commit_test_obligation(rt, owner_turn)
    provenance = InvocationProvenance(owner, "human", "chat")
    payload = {
        "objective":"Original objective", "run":False,
        "origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"],"obligation_ids":[obligation_id]},
        "steps":[{"capability_id":"memory.search","description":"Search once","input":{"query":"x"}}],
    }

    first = rt.capabilities.invoke("work.create", payload, provenance=provenance)
    second = rt.capabilities.invoke("work.create", payload, provenance=provenance)
    assert first.result["work_id"] == second.result["work_id"]
    assert len(rt.work_store.list()) == 1


def test_one_owner_turn_may_create_distinct_work_items(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Two jobs")['conversation_id']
    owner_turn = rt.chat_store.append_owner(cid, "Create two different responsibilities", principal_id=owner)
    obligation_id = _commit_test_obligation(rt, owner_turn)
    provenance = InvocationProvenance(owner, "human", "chat")
    base = {"run":False,"origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"],"obligation_ids":[obligation_id]}}
    a = rt.capabilities.invoke("work.create", {**base,"objective":"Job A","steps":[{"capability_id":"memory.search","input":{"query":"a"}}]}, provenance=provenance)
    b = rt.capabilities.invoke("work.create", {**base,"objective":"Job B","steps":[{"capability_id":"memory.search","input":{"query":"b"}}]}, provenance=provenance)
    assert a.result["work_id"] != b.result["work_id"]
    assert len(rt.work_store.list()) == 2


def test_self_restart_fails_closed_without_systemd_invocation_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    calls = []
    def fake_run(args, timeout=20):
        calls.append(list(args)); return _fake_systemd(args, timeout)
    monkeypatch.setattr(host_module, "_run", fake_run)
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id

    occurrence = rt.capabilities.invoke(
        "host.service.restart", {"unit":"atlas-api.service"},
        provenance=InvocationProvenance(owner, "human", "work"), work_id="work-x", step_id="step-x",
    )
    assert occurrence.status == "failed"
    assert occurrence.error_code == "self_restart_identity_unavailable"
    assert not [row for row in calls if row[:3] == ["systemctl", "--user", "restart"]]


def test_uncertain_dispatch_can_finish_chat_without_work_when_nothing_else_is_owed(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    calls = []
    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition("test.fire", "Dispatch once", "start", "external", {"type":"object","properties":{},"additionalProperties":False}),
        lambda payload: ScopeResolution("test/fire", {}, "Dispatch once"),
        lambda payload: (calls.append(1) or ActionResult(True, {"dispatched":True}, {"ok":True,"verification_pending":True})),
    ))
    rt.policy_store.set(principal_id=owner.principal_id, scope="test/fire", operation="start", decision="YES")
    cid = rt.chat_store.create_conversation("Dispatch")['conversation_id']
    rt.chat.provider = SequenceProvider(
        '{"kind":"capability","capability_id":"test.fire","input":{}}',
        '{"kind":"reply","reply":"The dispatch was accepted; its final outcome is still unresolved."}',
    )
    result = rt.chat.send(cid, "Dispatch it once", principal_id=owner.principal_id, defer_capture=True)
    assert result["turn"]["content"].endswith("still unresolved.")
    assert calls == [1]
    assert rt.work_store.list() == ()


def test_multi_read_chat_chain_does_not_become_work(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    for cid in ("test.read.one", "test.read.two"):
        rt.capabilities_registry.register(CapabilityRegistration(
            CapabilityDefinition(cid, cid, "read", "none", {"type":"object","properties":{},"additionalProperties":False}),
            lambda payload, _cid=cid: ScopeResolution(f"test/read/{_cid}", {}, _cid),
            lambda payload, _cid=cid: ActionResult(True, {"source":_cid}, {"ok":True}),
        ))
        rt.policy_store.set(principal_id=owner.principal_id, scope=f"test/read/{cid}", operation="read", decision="YES")
    conversation_id = rt.chat_store.create_conversation("Reads")['conversation_id']
    rt.chat.provider = SequenceProvider(
        '{"kind":"capability","capability_id":"test.read.one","input":{}}',
        '{"kind":"capability","capability_id":"test.read.two","input":{}}',
        '{"kind":"reply","reply":"I checked both sources."}',
    )
    result = rt.chat.send(conversation_id, "Check both sources", principal_id=owner.principal_id, defer_capture=True)
    assert result["turn"]["content"] == "I checked both sources."
    assert rt.work_store.list() == ()


def test_runtime_service_identity_can_be_derived_from_systemd_cgroup():
    from atlas_core.host import _service_unit_from_cgroup
    cgroup = "0::/user.slice/user-995.slice/user@995.service/app.slice/atlas-api.service\n"
    assert _service_unit_from_cgroup(cgroup) == "atlas-api.service"


def test_restart_fails_closed_when_runtime_service_identity_is_unknown(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_SERVICE_UNIT", raising=False)
    monkeypatch.setattr(host_module, "_current_service_unit", lambda: None)
    calls = []
    monkeypatch.setattr(host_module, "_run", lambda args, timeout=20: (calls.append(list(args)) or _fake_systemd(args, timeout)))
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    with pytest.raises(RuntimeError, match="service identity is unresolved"):
        rt.capabilities.invoke(
            "host.service.restart", {"unit":"demo.service"},
            provenance=InvocationProvenance(owner, "human", "chat"),
        )
    assert not [row for row in calls if row[:3] == ["systemctl", "--user", "restart"]]
    assert not [row for row in rt.actions_store.recent(limit=20) if row.capability_id == "host.service.restart"]


def test_configured_service_identity_must_match_cgroup_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "configured.service")
    monkeypatch.setattr(host_module, "_current_service_unit", lambda: "detected.service")
    with pytest.raises(RuntimeError, match="service identity mismatch"):
        build_runtime(tmp_path / "instance")


def test_cgroup_identity_never_falls_back_to_user_manager_service():
    from atlas_core.host import _service_unit_from_cgroup
    assert _service_unit_from_cgroup("0::/user.slice/user-995.slice/user@995.service/app.slice\n") is None
    assert _service_unit_from_cgroup("0::/user.slice/user-995.slice/user@995.service\n") is None


def test_chat_refuses_identical_replay_of_uncertain_action(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    calls = []
    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition("test.async.replay", "Async replay test", "start", "external",
                             {"type":"object","required":["target"],"properties":{"target":{"type":"string"}},"additionalProperties":False}),
        lambda payload: ScopeResolution("test/async/" + payload["target"], dict(payload), "Async replay test"),
        lambda payload: (calls.append(dict(payload)) or ActionResult(True, {"accepted":True}, {"ok":True,"verification_pending":True})),
    ))
    rt.policy_store.set(principal_id=owner.principal_id, scope="test/async/job", operation="start", decision="YES")
    cid = rt.chat_store.create_conversation("Replay")['conversation_id']
    rt.chat.provider = SequenceProvider(
        '{"kind":"capability","capability_id":"test.async.replay","input":{"target":"job"}}',
        '{"kind":"capability","capability_id":"test.async.replay","input":{"target":"job"}}',
        '{"kind":"reply","reply":"The existing dispatch remains unresolved; I did not replay it."}',
    )
    result = rt.chat.send(cid, "Start it once", principal_id=owner.principal_id, defer_capture=True)
    assert calls == [{"target":"job"}]
    assert result["turn"]["content"].startswith("The existing dispatch")
    second_prompt = json.loads(rt.chat.provider.requests[2].input)
    assert any(item.get("status") == "replay_refused" for item in second_prompt["tool_results"])


def test_work_completion_no_longer_creates_owner_chat_report(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Execution truth only")["conversation_id"]
    work = rt.work.create(
        "Search durable memory", [{"capability_id":"memory.search","input":{"query":"nothing"}}],
        owner_principal_id=owner,
    )
    assert rt.work.run(work.work_id)["status"] == "completed"
    assert rt.chat_store.turns(cid) == ()



def test_restart_rate_limit_is_not_row_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")
    monkeypatch.setattr(host_module, "_run", _fake_systemd)
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    provenance = InvocationProvenance(owner, "human", "work")
    first = rt.capabilities.invoke(
        "host.service.restart", {"unit":"atlas-api.service"}, provenance=provenance,
        work_id="work-first", step_id="step-first",
    )
    assert first.status == "uncertain"
    for index in range(60):
        row = rt.capabilities.invoke("memory.search", {"query":f"noise-{index}"}, provenance=provenance)
        assert row.status == "succeeded"
    second = rt.capabilities.invoke(
        "host.service.restart", {"unit":"atlas-api.service"}, provenance=provenance,
        work_id="work-second", step_id="step-second",
    )
    assert second.status == "failed"
    assert second.error_code == "self_restart_rate_limited"


def test_registry_drift_pauses_work_without_treating_arbitrary_keyerror_as_retryable(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition("test.temp.read", "Temporary capability", "read", "none", {"type":"object","properties":{},"additionalProperties":False}),
        lambda payload: ScopeResolution("test/temp", {}, "Temporary read"),
        lambda payload: ActionResult(True, {"ok":True}, {"ok":True}),
    ))
    rt.policy_store.set(principal_id=owner, scope="test/temp", operation="read", decision="YES")
    work = rt.work.create("Temporary work", [{"capability_id":"test.temp.read","input":{}}], owner_principal_id=owner)
    rt.capabilities_registry.unregister_prefix("test.temp")
    detail = rt.work.run(work.work_id)
    assert detail["status"] == "paused"
    assert detail["steps"][0]["status"] == "waiting"
    assert "capability unavailable: test.temp.read" in detail["steps"][0]["error"]


def test_repeated_durable_required_call_is_refused_without_second_gate_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")
    monkeypatch.setattr(host_module, "_run", _fake_systemd)
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Repeated refusal")['conversation_id']
    rt.chat.provider = SequenceProvider(
        '{"kind":"capability","capability_id":"host.service.restart","input":{"unit":"atlas-api.service"}}',
        '{"kind":"capability","capability_id":"host.service.restart","input":{"unit":"atlas-api.service"}}',
        '{"kind":"reply","reply":"I will not replay the refused restart outside durable Work."}',
    )
    result = rt.chat.send(cid, "Restart your API", principal_id=owner.principal_id, defer_capture=True)
    assert result["turn"]["content"].startswith("I will not replay")
    assert not [row for row in rt.actions_store.recent(limit=20) if row.capability_id == "host.service.restart"]
    prompt = json.loads(rt.chat.provider.requests[2].input)
    assert any(item.get("status") == "replay_refused" for item in prompt["tool_results"])


def test_host_status_surfaces_resolved_service_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setattr(host_module, "_current_service_unit", lambda: "atlas-api.service")
    rt = build_runtime(tmp_path / "instance")
    status = rt.host.status().output
    assert status["service_unit"] == "atlas-api.service"
    assert status["service_identity_source"] == "configured"


def test_interrupted_work_create_occurrence_reconciles_from_durable_work(tmp_path):
    from atlas_core.actions import ActionRequest
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Orchestration")['conversation_id']
    owner_turn = rt.chat_store.append_owner(cid, "Create durable work", principal_id=owner)
    obligation_id = _commit_test_obligation(rt, owner_turn)
    payload = {
        "objective":"Search durable memory", "run":True,
        "origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"],"obligation_ids":[obligation_id]},
        "steps":[{"capability_id":"memory.search","input":{"query":"x"}}],
    }
    created = rt.capabilities.invoke("work.create", payload, provenance=InvocationProvenance(owner, "human", "chat"))
    work_id = created.result["work_id"]
    request = ActionRequest(
        "work.create", "create", "atlas/work", payload,
        InvocationProvenance(owner, "human", "chat"), summary="Create Work",
    )
    orphan = rt.actions_store.create(request, decision="YES", revision=rt.policy_store.revision(), event_id=None, status="executing")
    rt.actions_store.recover_executing()
    assert rt.actions_store.get(orphan.occurrence_id).status == "uncertain"
    assert orphan.occurrence_id in rt.work.reconcile_orchestration_actions()
    resolved = rt.actions_store.get(orphan.occurrence_id)
    assert resolved.status == "succeeded"
    assert resolved.result["work_id"] == work_id


def test_interrupted_work_create_reconciles_without_pre_handoff_execution(tmp_path):
    from atlas_core.actions import ActionRequest
    from atlas_core.work.runtime import _chat_work_key
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Staged create")['conversation_id']
    owner_turn = rt.chat_store.append_owner(cid, "Create and run durable work", principal_id=owner)
    obligation_id = _commit_test_obligation(rt, owner_turn)
    steps = [{"capability_id":"memory.search","input":{"query":"x"}}]
    objective = "Search durable memory"
    origin = {"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"],"obligation_ids":[obligation_id]}
    work = rt.work.create(
        objective, steps, owner_principal_id=owner,
        metadata={"auto_resume_on_recovery":True,"chat_origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"],"work_key":_chat_work_key(owner_turn["turn_id"], objective, steps)}},
        obligation_ids=[obligation_id], stage=True,
    )
    payload = {"objective":objective,"steps":steps,"run":True,"origin":origin}
    orphan = rt.actions_store.create(
        ActionRequest("work.create","create","atlas/work",payload,InvocationProvenance(owner,"human","chat")),
        decision="YES",revision=rt.policy_store.revision(),event_id=None,status="executing",
    )
    rt.actions_store.recover_executing()
    assert rt.work.detail(work.work_id)["status"] == "staged"
    assert orphan.occurrence_id in rt.work.reconcile_orchestration_actions()
    assert rt.work.detail(work.work_id)["status"] == "staged"
    assert rt.actions_store.get(orphan.occurrence_id).status == "succeeded"
    assert not rt.actions_store.for_work_step(work.work_id, rt.work_store.steps(work.work_id)[0].step_id)
    rt.chat_store.append(cid, "assistant", "Staged.")
    rt.chat_store.mark_turn_completed(owner_turn["turn_id"])
    rt.chat_store.mark_response_handed_off(owner_turn["turn_id"])
    assert rt.work.promote_runnable() == (work.work_id,)
    assert rt.work.run_runnable()[0]["status"] == "completed"
