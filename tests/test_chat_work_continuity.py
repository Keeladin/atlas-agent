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
from atlas_core.providers import ModelResponse


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
