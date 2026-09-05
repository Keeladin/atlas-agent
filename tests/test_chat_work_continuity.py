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


def test_chat_promotes_self_restart_to_work_after_runtime_refusal(tmp_path, monkeypatch):
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
    rows = [row for row in rt.actions_store.recent(limit=30) if row.capability_id == "host.service.restart"]
    assert len(rows) == 1 and rows[0].work_id
    assert rows[0].status == "uncertain"
    work = rt.work_store.get(rows[0].work_id)
    assert work.status == "waiting"
    assert work.metadata["chat_origin"]["conversation_id"] == cid
    assert work.metadata["auto_resume_on_recovery"] is True
    prompt = json.loads(rt.chat.provider.requests[1].input)
    signal = prompt["tool_results"][0]
    assert signal["status"] == "durable_required"
    assert signal["scope"] == "host/service/atlas-api.service"


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


def test_recovery_reconciles_restart_then_resumes_verification_and_reports_to_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")
    monkeypatch.setattr(host_module, "_run", _fake_systemd)
    root = tmp_path / "instance"
    rt = build_runtime(root)
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Restart")['conversation_id']
    owner_turn = rt.chat_store.append(cid, "user", "Restart your API and verify it comes back healthy")
    work = rt.work.create(
        "Restart the Atlas API and verify it is running",
        [
            {"capability_id":"host.service.restart","description":"Restart Atlas API","input":{"unit":"atlas-api.service"}},
            {"capability_id":"host.service.status","description":"Verify Atlas API service state","input":{"unit":"atlas-api.service"}},
        ],
        owner_principal_id=owner.principal_id,
        metadata={"auto_resume_on_recovery":True,"chat_origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"]}},
    )
    waiting = rt.work.run(work.work_id)
    assert waiting["status"] == "waiting"
    restart_step = waiting["steps"][0]
    restart_occurrence = rt.actions_store.get(restart_step["occurrence_id"])
    assert restart_occurrence.receipt["predecessor_invocation_id"] == "old-invocation"

    monkeypatch.setenv("INVOCATION_ID", "new-invocation")
    def provider_must_not_run_during_boot(_self, _request):
        raise AssertionError("model provider was called before build_runtime returned")
    monkeypatch.setattr(ProviderRuntime, "generate", provider_must_not_run_during_boot)
    rt2 = build_runtime(root)
    detail = rt2.work.detail(work.work_id)
    assert detail["status"] == "completed"
    assert [step["status"] for step in detail["steps"]] == ["completed", "completed"]
    turns = rt2.chat_store.turns(cid)
    completions = [turn for turn in turns if turn["metadata"].get("work_completion")]
    assert len(completions) == 1
    assert "atlas-api.service is active/running" in completions[0]["content"]
    rt3 = build_runtime(root)
    completions_again = [turn for turn in rt3.chat_store.turns(cid) if turn["metadata"].get("work_completion")]
    assert len(completions_again) == 1


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
    owner_turn = rt.chat_store.append(cid, "user", "Do the durable thing")
    provenance = InvocationProvenance(owner, "human", "chat")
    payload = {
        "objective":"Original objective", "run":False,
        "origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"]},
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
    owner_turn = rt.chat_store.append(cid, "user", "Create two different responsibilities")
    provenance = InvocationProvenance(owner, "human", "chat")
    base = {"run":False,"origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"]}}
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


def test_normal_work_completion_posts_exactly_one_chat_turn(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Normal completion")['conversation_id']
    owner_turn = rt.chat_store.append(cid, "user", "Search durable memory")
    work = rt.work.create(
        "Search durable memory", [{"capability_id":"memory.search","input":{"query":"nothing"}}],
        owner_principal_id=owner.principal_id,
        metadata={"chat_origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"]}},
    )
    assert rt.work.run(work.work_id)["status"] == "completed"
    completions = [t for t in rt.chat_store.turns(cid) if t["metadata"].get("work_completion")]
    assert len(completions) == 1
    rt.work.run(work.work_id)
    assert len([t for t in rt.chat_store.turns(cid) if t["metadata"].get("work_completion")]) == 1


def test_work_completion_is_durable_before_model_reporting_and_upgrades_in_place(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Completion report")["conversation_id"]
    owner_turn = rt.chat_store.append(cid, "user", "Restart, verify, then report weather")
    work = rt.work_store.create(
        "Restart the API, verify health, and report the Cullinan weather", owner,
        [
            {"capability_id": "host.service.restart", "description": "Restart", "input": {"unit": "atlas-api.service"}},
            {"capability_id": "host.service.status", "description": "Verify", "input": {"unit": "atlas-api.service"}},
            {"capability_id": "web.fetch", "description": "Weather", "input": {"url": "https://wttr.in/Cullinan?format=3"}},
        ],
        metadata={"chat_origin": {"conversation_id": cid, "owner_turn_id": owner_turn["turn_id"]}},
    )
    steps = rt.work_store.steps(work.work_id)
    rt.work_store.set_step(steps[0].step_id, status="completed", occurrence_id="action_restart", output={"unit": "atlas-api.service", "dispatched": True})
    rt.work_store.set_step(steps[1].step_id, status="completed", occurrence_id="action_status", output={"unit": "atlas-api.service", "properties": {"ActiveState": "active", "SubState": "running"}})
    rt.work_store.set_step(steps[2].step_id, status="completed", occurrence_id="action_weather", output={"payload": {"text": "Cullinan: 24°C, clear"}})
    rt.work_store.set_work_status(work.work_id, "completed")
    detail = rt.work.detail(work.work_id)
    rt.chat.provider = SequenceProvider(
        '{"kind":"reply","reply":"The API restarted and verified healthy. Cullinan weather: 24°C and clear."}',
        '{"grounded":true,"unsupported_claims":[]}',
    )

    initial = rt.chat.record_work_completion(detail)
    assert initial is not None
    assert initial["metadata"]["completion_report"]["mode"] == "deterministic_pending"
    assert "24°C" not in initial["content"]
    assert rt.chat.provider.requests == []

    changed = rt.chat.upgrade_pending_work_completion_reports()
    assert len(changed) == 1
    completions = [t for t in rt.chat_store.turns(cid) if t["metadata"].get("work_completion")]
    assert len(completions) == 1
    upgraded = completions[0]
    assert upgraded["turn_id"] == initial["turn_id"]
    assert "24°C and clear" in upgraded["content"]
    assert upgraded["metadata"]["completion_report"]["mode"] == "grounded_model_verified"
    assert upgraded["metadata"]["work_completion"]["final_output"] == {"payload": {"text": "Cullinan: 24°C, clear"}}
    assert rt.chat.provider.requests[0].capability_id == "chat.work_completion_report"
    assert rt.chat.provider.requests[1].capability_id == "chat.work_completion_verify"


def test_completion_persistence_never_calls_model_and_fallback_does_not_leak_raw_output(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    cid = rt.chat_store.create_conversation("No network at boot")["conversation_id"]

    class BrokenProvider:
        def generate(self, _request):
            raise AssertionError("model provider must not run while completion truth is being persisted")

    rt.chat.provider = BrokenProvider()
    detail = {
        "work_id": "work_boot_safe", "objective": "Finish a recovered responsibility", "status": "completed",
        "revision": 1, "updated_at": "2026-09-05T05:00:00+00:00",
        "metadata": {"chat_origin": {"conversation_id": cid, "owner_turn_id": "turn_owner"}},
        "steps": [{
            "ordinal": 1, "description": "Opaque final step", "capability_id": "test.internal", "status": "completed",
            "output": {"transport_internal": "SECRET STACK TRACE SHOULD NOT BE SHOWN", "code": 500},
            "occurrence_id": "action_final",
        }],
    }
    turn = rt.chat.record_work_completion(detail)
    assert turn is not None
    assert "SECRET STACK TRACE" not in turn["content"]
    assert "Recorded step results are available in the Work item" in turn["content"]
    assert turn["metadata"]["completion_report"]["mode"] == "deterministic_pending"


def test_unverified_completion_report_never_replaces_deterministic_turn(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Rejected report")["conversation_id"]
    owner_turn = rt.chat_store.append(cid, "user", "Do the durable thing")
    work = rt.work_store.create(
        "Do the durable thing", owner,
        [{"capability_id": "memory.search", "description": "Search", "input": {"query": "x"}}],
        metadata={"chat_origin": {"conversation_id": cid, "owner_turn_id": owner_turn["turn_id"]}},
    )
    step = rt.work_store.steps(work.work_id)[0]
    rt.work_store.set_step(step.step_id, status="completed", occurrence_id="action_search", output={"items": []})
    rt.work_store.set_work_status(work.work_id, "completed")
    initial = rt.chat.record_work_completion(rt.work.detail(work.work_id))
    rt.chat.provider = SequenceProvider(
        '{"kind":"reply","reply":"I also proved an unrelated fact that is not in evidence."}',
        '{"grounded":false,"unsupported_claims":["unrelated fact"]}',
    )
    rt.chat.upgrade_pending_work_completion_reports()
    final = [t for t in rt.chat_store.turns(cid) if t["metadata"].get("work_completion")][0]
    assert final["turn_id"] == initial["turn_id"]
    assert final["content"] == initial["content"]
    assert final["metadata"]["completion_report"]["mode"] == "deterministic_fallback"
    assert final["metadata"]["completion_report"]["verification"]["grounded"] is False


def test_completion_reporting_failure_never_flips_completed_work(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    work = rt.work.create(
        "Search durable memory", [{"capability_id": "memory.search", "input": {"query": "nothing"}}],
        owner_principal_id=owner,
    )

    def broken_reporter(_detail):
        raise RuntimeError("report persistence unavailable")

    rt.work.set_completion_hook(broken_reporter)
    detail = rt.work.run(work.work_id)
    assert detail["status"] == "completed"
    assert detail["steps"][0]["status"] == "completed"
    reporting = detail["metadata"]["completion_reporting"]
    assert reporting["status"] == "failed"
    assert reporting["error_type"] == "RuntimeError"
    assert "report persistence unavailable" in reporting["error"]


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
    owner_turn = rt.chat_store.append(cid, "user", "Create durable work")
    payload = {
        "objective":"Search durable memory", "run":True,
        "origin":{"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"]},
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


def test_interrupted_work_create_resumes_queued_created_work_before_reconciling(tmp_path):
    from atlas_core.actions import ActionRequest
    from atlas_core.work.runtime import _chat_work_key
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Queued create")['conversation_id']
    owner_turn = rt.chat_store.append(cid, "user", "Create and run durable work")
    steps = [{"capability_id":"memory.search","input":{"query":"x"}}]
    objective = "Search durable memory"
    origin = {"conversation_id":cid,"owner_turn_id":owner_turn["turn_id"]}
    work = rt.work.create(
        objective, steps, owner_principal_id=owner,
        metadata={"auto_resume_on_recovery":True,"chat_origin":{**origin,"work_key":_chat_work_key(owner_turn["turn_id"], objective, steps)}},
    )
    payload = {"objective":objective,"steps":steps,"run":True,"origin":origin}
    orphan = rt.actions_store.create(
        ActionRequest("work.create","create","atlas/work",payload,InvocationProvenance(owner,"human","chat")),
        decision="YES",revision=rt.policy_store.revision(),event_id=None,status="executing",
    )
    rt.actions_store.recover_executing()
    assert rt.work.detail(work.work_id)["status"] == "queued"
    assert orphan.occurrence_id in rt.work.reconcile_orchestration_actions()
    assert rt.work.detail(work.work_id)["status"] == "completed"
    assert rt.actions_store.get(orphan.occurrence_id).status == "succeeded"
