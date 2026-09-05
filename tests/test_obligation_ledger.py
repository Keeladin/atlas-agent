from __future__ import annotations

import ast
import asyncio
import inspect
import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import atlas_core.host as host_module

from atlas_api.compose import build_runtime
from atlas_api.handoff import ResponseHandoffMiddleware
from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, ScopeResolution
from atlas_core.chat import ChatStore
from atlas_core.obligations import (
    ObligationIntakeRuntime,
    ObligationStore,
    RuntimeInvariantError,
    collect_runtime_violations,
)
from atlas_core.providers import ModelResponse
from atlas_core.retrieval.capabilities import registry_fingerprint


class ScriptProvider:
    def __init__(self, *, intake=None, planner=(), communication_verify=None,
                 state_verify=None, report=None, report_verify=None):
        self.intake = intake
        self.planner = list(planner)
        self.communication_verify = communication_verify
        self.state_verify = state_verify
        self.report = report
        self.report_verify = report_verify
        self.requests = []

    @staticmethod
    def _value(value, request):
        return value(request) if callable(value) else value

    def generate(self, request):
        self.requests.append(request)
        if request.capability_id == "chat.obligation_intake":
            text = self._value(self.intake, request)
        elif request.capability_id == "chat.communication_delivery_verify":
            value = self.communication_verify
            text = self._value(value, request) if value is not None else json.dumps({
                "grounded": False, "fulfilled_obligation_ids": [], "unsupported_claims": []
            })
        elif request.capability_id == "obligation.state_change_verify":
            value = self.state_verify
            text = self._value(value, request) if value is not None else json.dumps({
                "fulfilled": True, "reason": "The bound successful action evidence proves the requested outcome."
            })
        elif request.capability_id == "chat.obligation_report":
            value = self.report
            text = self._value(value, request) if value is not None else json.dumps({
                "kind": "reply", "reply": "The requested result is now available."
            })
        elif request.capability_id == "chat.obligation_report_verify":
            value = self.report_verify
            text = self._value(value, request) if value is not None else json.dumps({
                "grounded": True, "unsupported_claims": []
            })
        else:
            if not self.planner:
                raise AssertionError(f"unexpected planner request: {request.capability_id}")
            text = self._value(self.planner.pop(0), request)
        return ModelResponse(text=str(text), provider_key="test", model="test-model", raw={})


def _stores(tmp_path):
    path = tmp_path / "chat.db"
    chat = ChatStore(path); chat.initialize()
    obligations = ObligationStore(path); obligations.initialize()
    cid = chat.create_conversation("Ledger")["conversation_id"]
    return chat, obligations, cid


def _commit(rt, message: str, *, kind: str = "state_change", temporal: str | None = None):
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Obligation")["conversation_id"]
    turn = rt.chat_store.append_owner(cid, message, principal_id=owner)
    attempt = rt.obligation_store.begin_attempt(turn["turn_id"])
    item = {"grounding_excerpt": message, "text": message, "kind": kind}
    if temporal is not None:
        item["temporal_grounding_excerpt"] = temporal
    result = rt.obligation_store.commit_intake(
        turn["turn_id"], [item], attempts=attempt, provider="test", model="test"
    )
    return owner, cid, rt.chat_store.turn(turn["turn_id"]), result.obligation_ids[0]


def _register_effect(rt, *, capability_id="test.effect", scope="test/effect", operation="apply",
                     effect_class="external", output=None):
    owner = rt.identities.current_owner().principal_id
    calls = []

    def execute(payload):
        calls.append(dict(payload))
        return ActionResult(True, output if output is not None else {"changed": True}, {"ok": True})

    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition(
            capability_id, capability_id, operation, effect_class,
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        lambda payload: ScopeResolution(scope, dict(payload), capability_id),
        execute,
        metadata={"scope_hint": scope},
    ), replace=True)
    rt.policy_store.set(principal_id=owner, scope=scope, operation=operation, decision="YES")
    return calls


def _mapped_step(capability_id: str, obligation_id: str, *, description: str | None = None):
    return {
        "capability_id": capability_id,
        "description": description or capability_id,
        "input": {},
        "obligation_ids": [obligation_id],
    }


def test_owner_turn_starts_fail_closed_and_zero_obligation_greeting_completes(tmp_path):
    chat, obligations, cid = _stores(tmp_path)
    turn = chat.append_owner(cid, "hi", principal_id="owner-1")
    assert turn["intake_status"] == "failed"
    assert turn["intake_error_code"] == "intake_not_completed"
    provider = ScriptProvider(intake='{"obligations":[],"unmapped_spans":[]}')
    result = ObligationIntakeRuntime(obligations, provider).capture(turn)
    assert result.status == "complete"
    assert result.obligation_ids == ()
    assert obligations.for_turn(turn["turn_id"]) == ()


def test_three_outcome_turn_commits_obligations_before_staged_execution(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    for cid in ("test.restart", "test.health", "test.weather"):
        _register_effect(rt, capability_id=cid, scope=f"test/{cid}", operation=cid.split(".")[-1])
    conversation = rt.chat_store.create_conversation("Three outcomes")["conversation_id"]
    message = (
        "Restart your API service, then verify that it came back healthy. "
        "Then tell me what the weather is like today in Cullinan."
    )
    intake = json.dumps({
        "obligations": [
            {"grounding_excerpt": "Restart your API service", "text": "Restart the API service", "kind": "state_change"},
            {"grounding_excerpt": "verify that it came back healthy", "text": "Verify the API is healthy", "kind": "state_change"},
            {"grounding_excerpt": "tell me what the weather is like today in Cullinan", "text": "Report today's Cullinan weather", "kind": "communication"},
        ],
        "unmapped_spans": [],
    })

    def plan_work(request):
        prompt = json.loads(request.input)
        rows = prompt["owner_obligations"]
        assert len(rows) == 3
        return json.dumps({
            "kind": "capability", "capability_id": "work.create", "input": {
                "objective": "Restart, verify, then gather Cullinan weather",
                "steps": [
                    _mapped_step("test.restart", rows[0]["obligation_id"], description="Restart"),
                    _mapped_step("test.health", rows[1]["obligation_id"], description="Verify health"),
                    _mapped_step("test.weather", rows[2]["obligation_id"], description="Gather weather"),
                ],
                "run": True,
            }
        })

    provider = ScriptProvider(
        intake=intake,
        planner=[plan_work, '{"kind":"reply","reply":"I have staged the ordered work."}'],
    )
    rt.chat.provider = provider; rt.obligation_intake.provider = provider
    result = rt.chat.send(conversation, message, principal_id=owner, defer_capture=True)
    owner_turn = rt.chat_store.turn(result["_owner_turn_id"])
    obligations = rt.obligation_store.for_turn(owner_turn["turn_id"])
    assert [item.kind for item in obligations] == ["state_change", "state_change", "communication"]
    assert owner_turn["intake_status"] == "complete"
    assert provider.requests[0].capability_id == "chat.obligation_intake"
    assert provider.requests[1].capability_id == "chat.turn"
    work = rt.work_store.list(limit=10)[0]
    assert work.status == "staged"
    assert len(rt.work_store.bindings(work.work_id)) == 3
    assert not [x for x in rt.actions_store.recent(limit=50) if x.capability_id.startswith("test.")]


def test_invalid_grounding_is_rejected(tmp_path):
    chat, obligations, cid = _stores(tmp_path)
    turn = chat.append_owner(cid, "Restart Atlas", principal_id="owner-1")
    attempt = obligations.begin_attempt(turn["turn_id"])
    with pytest.raises(ValueError, match="grounding excerpt"):
        obligations.commit_intake(
            turn["turn_id"],
            [{"grounding_excerpt": "Delete Atlas", "text": "Delete Atlas", "kind": "state_change"}],
            attempts=attempt, provider="test", model="test",
        )
    assert obligations.for_turn(turn["turn_id"]) == ()


def test_intake_failure_never_reaches_planning_or_execution(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Bad intake")["conversation_id"]
    provider = ScriptProvider(intake="not-json", planner=['{"kind":"reply","reply":"should not run"}'])
    rt.chat.provider = provider; rt.obligation_intake.provider = provider
    result = rt.chat.send(cid, "Restart Atlas", principal_id=owner)
    turn = next(item for item in rt.chat_store.turns(cid) if item["role"] == "user")
    assert turn["intake_status"] == "failed"
    assert turn["intake_attempts"] == 2
    assert rt.obligation_store.for_turn(turn["turn_id"]) == ()
    assert all(request.capability_id == "chat.obligation_intake" for request in provider.requests)
    assert rt.actions_store.recent(limit=20) == ()
    assert "didn't execute anything" in result["turn"]["content"]


def test_partial_intake_persists_grounded_subset_but_dispatches_nothing(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Partial")["conversation_id"]
    message = "Restart Atlas, but only after the report is saved"
    provider = ScriptProvider(intake=json.dumps({
        "obligations": [{"grounding_excerpt": "Restart Atlas", "text": "Restart Atlas", "kind": "state_change"}],
        "unmapped_spans": ["but only after the report is saved"],
    }))
    rt.chat.provider = provider; rt.obligation_intake.provider = provider
    result = rt.chat.send(cid, message, principal_id=owner)
    turn = next(item for item in rt.chat_store.turns(cid) if item["role"] == "user")
    assert turn["intake_status"] == "partial"
    assert turn["unmapped_spans"] == ["but only after the report is saved"]
    assert len(rt.obligation_store.for_turn(turn["turn_id"])) == 1
    assert rt.actions_store.recent(limit=20) == ()
    assert "didn't execute anything" in result["turn"]["content"]


def test_restart_after_obligation_commit_before_dispatch_preserves_duty_without_occurrence(tmp_path):
    root = tmp_path / "instance"
    rt = build_runtime(root)
    _owner, _cid, _turn, obligation_id = _commit(rt, "Apply the requested change")
    assert rt.actions_store.recent(limit=20) == ()
    rt2 = build_runtime(root)
    assert rt2.obligation_store.get(obligation_id).status == "open"
    assert rt2.actions_store.recent(limit=20) == ()


def test_binding_and_work_completion_are_not_sufficient_without_evidence_verification(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, _turn, obligation_id = _commit(rt, "Make the requested state change")
    _register_effect(rt)
    work = rt.work.create(
        "Attempt service", [_mapped_step("test.effect", obligation_id)], owner_principal_id=owner
    )
    assert rt.work.run(work.work_id)["status"] == "completed"
    rt.obligation_reconciler.provider = ScriptProvider(
        state_verify='{"fulfilled":false,"reason":"The evidence does not prove the requested outcome."}'
    )
    assert rt.obligation_reconciler.reconcile_noncommunication() == ()
    assert rt.obligation_store.get(obligation_id).status == "open"


def test_one_completed_obligation_does_not_make_turn_complete_while_another_is_open(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Two duties")["conversation_id"]
    turn = rt.chat_store.append_owner(cid, "Do A and do B", principal_id=owner)
    attempt = rt.obligation_store.begin_attempt(turn["turn_id"])
    result = rt.obligation_store.commit_intake(
        turn["turn_id"], [
            {"grounding_excerpt": "Do A", "text": "Do A", "kind": "state_change"},
            {"grounding_excerpt": "do B", "text": "Do B", "kind": "state_change"},
        ], attempts=attempt, provider="test", model="test",
    )
    _register_effect(rt)
    work = rt.work.create(
        "Do A", [_mapped_step("test.effect", result.obligation_ids[0])], owner_principal_id=owner
    )
    rt.work.run(work.work_id)
    rt.obligation_reconciler.provider = ScriptProvider()
    assert rt.obligation_reconciler.reconcile_noncommunication() == (result.obligation_ids[0],)
    assert [x.obligation_id for x in rt.obligation_store.open_for_turn(turn["turn_id"])] == [result.obligation_ids[1]]


def test_policy_no_resolves_only_the_obligation_bound_to_the_blocked_step(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Policy")["conversation_id"]
    turn = rt.chat_store.append_owner(cid, "Do A and do B", principal_id=owner)
    attempt = rt.obligation_store.begin_attempt(turn["turn_id"])
    ids = rt.obligation_store.commit_intake(
        turn["turn_id"], [
            {"grounding_excerpt": "Do A", "text": "Do A", "kind": "state_change"},
            {"grounding_excerpt": "do B", "text": "Do B", "kind": "state_change"},
        ], attempts=attempt, provider="test", model="test",
    ).obligation_ids
    _register_effect(rt, capability_id="test.a", scope="test/a", operation="apply-a")
    _register_effect(rt, capability_id="test.b", scope="test/b", operation="apply-b")
    rt.policy_store.set(principal_id=owner, scope="test/a", operation="apply-a", decision="NO")
    work = rt.work.create(
        "Do both", [_mapped_step("test.a", ids[0]), _mapped_step("test.b", ids[1])], owner_principal_id=owner
    )
    assert rt.work.run(work.work_id)["status"] == "paused"
    assert rt.obligation_reconciler.reconcile_noncommunication() == (ids[0],)
    first = rt.obligation_store.get(ids[0]); second = rt.obligation_store.get(ids[1])
    assert first.resolution_kind == "declined_policy" and first.resolution_ref.startswith("action:")
    assert second.status == "open"


def test_work_cancellation_leaves_obligation_open_and_attention_derived_from_ledger(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, _turn, obligation_id = _commit(rt, "Keep responsibility for this")
    _register_effect(rt)
    work = rt.work.create(
        "Disposable mechanism", [_mapped_step("test.effect", obligation_id)], owner_principal_id=owner
    )
    rt.work.cancel(work.work_id)
    assert rt.obligation_store.get(obligation_id).status == "open"
    assert any(
        row["kind"] == "unserviced_obligation" and row["obligation_id"] == obligation_id
        for row in rt.attention.snapshot()
    )


def test_unbound_obligation_survives_restart_and_remains_attention(tmp_path):
    root = tmp_path / "instance"
    rt = build_runtime(root)
    _owner, _cid, _turn, obligation_id = _commit(rt, "Keep this dangling duty")
    rt2 = build_runtime(root)
    assert rt2.obligation_store.get(obligation_id).status == "open"
    assert any(row["obligation_id"] == obligation_id for row in rt2.attention.snapshot())


def _completed_support(rt, owner, obligation_id, *, capability_id="test.effect"):
    _register_effect(rt, capability_id=capability_id, scope=f"test/{capability_id}", operation=f"run-{capability_id}")
    work = rt.work.create(
        "Gather supporting evidence", [_mapped_step(capability_id, obligation_id)], owner_principal_id=owner
    )
    assert rt.work.run(work.work_id)["status"] == "completed"
    return work


def test_communication_stays_open_until_verified_owner_facing_turn_is_persisted(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, _turn, obligation_id = _commit(rt, "Tell me the recorded result", kind="communication")
    _completed_support(rt, owner, obligation_id)
    assert rt.obligation_store.get(obligation_id).status == "open"
    rt.obligation_reconciler.provider = ScriptProvider(
        report='{"kind":"reply","reply":"The recorded result is available."}',
        report_verify='{"grounded":true,"unsupported_claims":[]}',
    )
    reports = rt.obligation_reconciler.report_communications()
    assert len(reports) == 1
    resolved = rt.obligation_store.get(obligation_id)
    assert resolved.status == "resolved" and resolved.resolution_ref == f"chat_turn:{reports[0]}"
    assert rt.chat_store.turn(reports[0])["conversation_id"] == cid


def test_failed_report_verification_leaves_communication_open(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, _turn, obligation_id = _commit(rt, "Tell me the verified result", kind="communication")
    _completed_support(rt, owner, obligation_id)
    rt.obligation_reconciler.provider = ScriptProvider(
        report='{"kind":"reply","reply":"Unsupported result."}',
        report_verify='{"grounded":false,"unsupported_claims":["Unsupported result"]}',
    )
    assert rt.obligation_reconciler.report_communications() == ()
    assert rt.obligation_store.get(obligation_id).status == "open"


def _commit_items(rt, message: str, items, *, conversation_id: str | None = None):
    owner = rt.identities.current_owner().principal_id
    cid = conversation_id or rt.chat_store.create_conversation("Obligation")["conversation_id"]
    turn = rt.chat_store.append_owner(cid, message, principal_id=owner)
    attempt = rt.obligation_store.begin_attempt(turn["turn_id"])
    result = rt.obligation_store.commit_intake(
        turn["turn_id"], items, attempts=attempt, provider="test", model="test"
    )
    return owner, cid, rt.chat_store.turn(turn["turn_id"]), tuple(result.obligation_ids)


_RESTART_TURN = "Restart the service and tell me when it is up"
_RESTART_ITEMS = [
    {"grounding_excerpt": "Restart the service", "text": "Restart the service", "kind": "state_change"},
    {"grounding_excerpt": "tell me when it is up", "text": "Tell me when it is up", "kind": "communication"},
]


def _report_basis(provider):
    request = next(item for item in provider.requests if item.capability_id == "chat.obligation_report")
    return json.loads(request.input)


def test_report_waits_for_sibling_state_change_verification(tmp_path):
    """The incident: a serviced, evidenced action must never be reported as outstanding."""
    rt = build_runtime(tmp_path / "instance")
    owner, cid, turn, (state_id, comm_id) = _commit_items(rt, _RESTART_TURN, _RESTART_ITEMS)
    _completed_support(rt, owner, state_id, capability_id="test.restart")
    _completed_support(rt, owner, comm_id, capability_id="test.observe")

    verdicts = [
        '{"fulfilled":false,"reason":"only dispatch is proven"}',
        '{"fulfilled":true,"reason":"the bound execution receipt proves the restart"}',
    ]
    rt.obligation_reconciler.provider = ScriptProvider(
        state_verify=lambda _request: verdicts.pop(0),
        report='{"kind":"reply","reply":"The service was restarted and is up."}',
        report_verify='{"grounded":true,"unsupported_claims":[]}',
    )

    assert rt.obligation_reconciler.reconcile_noncommunication() == ()
    assert rt.obligation_reconciler.report_communications() == ()
    assert rt.obligation_store.get(comm_id).status == "open"
    assert [row for row in rt.chat_store.turns(cid) if row["role"] == "assistant"] == []
    assert not [item for item in rt.obligation_reconciler.provider.requests
                if item.capability_id == "chat.obligation_report"]

    assert rt.obligation_reconciler.reconcile_noncommunication() == (state_id,)
    reports = rt.obligation_reconciler.report_communications()
    assert len(reports) == 1
    basis = _report_basis(rt.obligation_reconciler.provider)
    assert basis["still_open"] == []
    assert [row["obligation_id"] for row in basis["fulfilled_in_turn"]] == [state_id]
    assert basis["fulfilled_in_turn"][0]["evidence"][0]["capability_id"] == "test.restart"
    meta = rt.chat_store.turn(reports[0])["metadata"]["obligation_report"]
    assert meta["owner_turn_id"] == turn["turn_id"]
    assert meta["outstanding_obligation_states"] == {}


def test_report_labels_unserviced_outstanding_obligation(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, turn, (state_id, comm_id) = _commit_items(rt, _RESTART_TURN, _RESTART_ITEMS)
    _completed_support(rt, owner, comm_id, capability_id="test.observe")
    rt.obligation_reconciler.provider = ScriptProvider(
        report='{"kind":"reply","reply":"Observed. The restart is still outstanding."}',
        report_verify='{"grounded":true,"unsupported_claims":[]}',
    )
    reports = rt.obligation_reconciler.report_communications()
    assert len(reports) == 1
    assert _report_basis(rt.obligation_reconciler.provider)["still_open"] == [
        {"obligation_id": state_id, "text": "Restart the service",
         "kind": "state_change", "servicing_state": "unserviced"}
    ]
    meta = rt.chat_store.turn(reports[0])["metadata"]["obligation_report"]
    assert meta["owner_turn_id"] == turn["turn_id"]
    assert meta["outstanding_obligation_states"] == {state_id: "unserviced"}


def test_waiting_owner_turn_does_not_hold_an_unrelated_turn_report(tmp_path):
    """Reporting is scoped to one owner turn; a waiting sibling holds only its own turn."""
    rt = build_runtime(tmp_path / "instance")
    owner, cid, first_turn, (state_id, first_comm_id) = _commit_items(rt, _RESTART_TURN, _RESTART_ITEMS)
    _completed_support(rt, owner, state_id, capability_id="test.restart")
    _completed_support(rt, owner, first_comm_id, capability_id="test.observe")
    second_message = "Also tell me the recorded disk usage"
    _owner, _cid, second_turn, (second_comm_id,) = _commit_items(
        rt, second_message,
        [{"grounding_excerpt": "tell me the recorded disk usage",
          "text": "Report the recorded disk usage", "kind": "communication"}],
        conversation_id=cid,
    )
    _completed_support(rt, owner, second_comm_id, capability_id="test.disk")

    rt.obligation_reconciler.provider = ScriptProvider(
        state_verify='{"fulfilled":false,"reason":"only dispatch is proven"}',
        report='{"kind":"reply","reply":"The recorded disk usage is available."}',
        report_verify='{"grounded":true,"unsupported_claims":[]}',
    )
    reports = rt.obligation_reconciler.report_communications()
    assert len(reports) == 1
    assert rt.obligation_store.get(second_comm_id).status == "resolved"
    assert rt.obligation_store.get(first_comm_id).status == "open"
    assert rt.chat_store.turn(reports[0])["metadata"]["obligation_report"]["owner_turn_id"] == second_turn["turn_id"]
    assert first_turn["turn_id"] != second_turn["turn_id"]


def test_report_outstanding_over_fulfilled_evidence_is_a_forbidden_state(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, _turn, (state_id, _comm_id) = _commit_items(rt, _RESTART_TURN, _RESTART_ITEMS)
    _completed_support(rt, owner, state_id, capability_id="test.restart")

    def _forge(turn_id: str, report: dict) -> None:
        with sqlite3.connect(rt.chat_store.path) as db:
            db.execute(
                "INSERT INTO chat_turns(turn_id,conversation_id,role,content,metadata_json) VALUES (?,?,?,?,?)",
                (turn_id, cid, "assistant", "Still outstanding.",
                 json.dumps({"obligation_report": report})),
            )

    base = {"obligation_ids": [], "obligation_revisions": {}, "evidence_ids": [],
            "outstanding_obligation_revisions": {state_id: 1}}
    _forge("turn_mislabelled", {**base, "outstanding_obligation_states": {state_id: "unserviced"}})
    _forge("turn_unlabelled", dict(base))

    violations = collect_runtime_violations(
        rt.chat_store, rt.obligation_store, rt.work_store, rt.actions_store, rt.evidence
    )
    mislabelled = [item for item in violations
                   if item.code == "report_outstanding_over_fulfilled_evidence"]
    assert {item.reference for item in mislabelled} == {"turn_mislabelled", "turn_unlabelled"}
    assert any(state_id in item.detail for item in mislabelled)


def test_report_snapshot_change_discards_candidate(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    message = "Tell me the result within 1 second"
    owner, _cid, _turn, obligation_id = _commit(rt, message, kind="communication", temporal="within 1 second")
    _completed_support(rt, owner, obligation_id)

    def race_verify(_request):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        rt.obligation_store.observe_lapses(future)
        return '{"grounded":true,"unsupported_claims":[]}'

    rt.obligation_reconciler.provider = ScriptProvider(
        report='{"kind":"reply","reply":"The result is ready."}', report_verify=race_verify
    )
    assert rt.obligation_reconciler.report_communications() == ()
    current = rt.obligation_store.get(obligation_id)
    assert current.status == "open" and current.lapsed_at is not None


def test_staged_work_never_runs_before_confirmed_response_handoff(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, turn, obligation_id = _commit(rt, "Apply the durable change")
    _register_effect(rt)
    work = rt.work.create(
        "Apply later", [_mapped_step("test.effect", obligation_id)], owner_principal_id=owner,
        metadata={"chat_origin": {"conversation_id": cid, "owner_turn_id": turn["turn_id"]}}, stage=True,
    )
    rt.chat_store.append(cid, "assistant", "The work is staged.")
    rt.chat_store.mark_turn_completed(turn["turn_id"])
    assert rt.work.promote_runnable() == ()
    assert rt.work_store.get(work.work_id).status == "waiting"
    assert not [x for x in rt.actions_store.recent(limit=20) if x.capability_id == "test.effect"]
    rt.chat_store.mark_response_handed_off(turn["turn_id"])
    assert rt.work.promote_runnable() == (work.work_id,)
    assert rt.work.run_runnable()[0]["status"] == "completed"


def test_handoff_stamp_failure_is_loud_and_leaves_waiting_attention(tmp_path, caplog):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, turn, obligation_id = _commit(rt, "Apply after response handoff")
    _register_effect(rt)
    work = rt.work.create(
        "Wait for handoff", [_mapped_step("test.effect", obligation_id)], owner_principal_id=owner,
        metadata={"chat_origin": {"conversation_id": cid, "owner_turn_id": turn["turn_id"]}}, stage=True,
    )
    rt.chat_store.append(cid, "assistant", "Staged.")
    rt.chat_store.mark_turn_completed(turn["turn_id"])

    class FailingStore:
        def mark_response_handed_off(self, _turn_id):
            raise RuntimeError("simulated handoff stamp failure")

    async def inner(scope, receive, send):
        scope.setdefault("state", {})["handoff_owner_turn_id"] = turn["turn_id"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    sent = []
    async def send(message): sent.append(message)
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    middleware = ResponseHandoffMiddleware(inner, chat_store=FailingStore())
    with caplog.at_level("CRITICAL"):
        asyncio.run(middleware({"type": "http", "state": {}}, receive, send))
    assert "response handoff stamp failed" in caplog.text
    assert rt.chat_store.turn(turn["turn_id"])["response_handed_off_at"] is None
    rt.work.promote_runnable()
    assert rt.work_store.get(work.work_id).status == "waiting"
    assert any(row["kind"] == "handoff_unconfirmed" for row in rt.attention.snapshot())


def test_recovery_keeps_handoff_unconfirmed_and_partial_work_nonrunnable(tmp_path):
    root = tmp_path / "instance"
    rt = build_runtime(root)
    owner, cid, turn, obligation_id = _commit(rt, "Do the durable thing")
    _register_effect(rt)
    work = rt.work.create(
        "Durable", [_mapped_step("test.effect", obligation_id)], owner_principal_id=owner,
        metadata={"chat_origin": {"conversation_id": cid, "owner_turn_id": turn["turn_id"]}}, stage=True,
    )
    rt.chat_store.append(cid, "assistant", "Staged.")
    rt.chat_store.mark_turn_completed(turn["turn_id"])
    rt2 = build_runtime(root)
    _register_effect(rt2)
    rt2.work.promote_runnable()
    assert rt2.work_store.get(work.work_id).status == "waiting"
    assert not rt2.actions_store.for_work_step(work.work_id, rt2.work_store.steps(work.work_id)[0].step_id)

    cid2 = rt2.chat_store.create_conversation("Partial recovery")["conversation_id"]
    turn2 = rt2.chat_store.append_owner(cid2, "Do A, but maybe not now", principal_id=owner)
    attempt = rt2.obligation_store.begin_attempt(turn2["turn_id"])
    result = rt2.obligation_store.commit_intake(
        turn2["turn_id"], [{"grounding_excerpt": "Do A", "text": "Do A", "kind": "state_change"}],
        attempts=attempt, provider="test", model="test", unmapped_spans=["but maybe not now"],
    )
    partial_id = result.obligation_ids[0]
    partial_work = rt2.work.create(
        "Partial", [_mapped_step("test.effect", partial_id)], owner_principal_id=owner,
        metadata={"chat_origin": {"conversation_id": cid2, "owner_turn_id": turn2["turn_id"]}}, stage=True,
    )
    rt2.chat_store.append(cid2, "assistant", "Not executable.")
    rt2.chat_store.mark_turn_completed(turn2["turn_id"])
    rt2.chat_store.mark_response_handed_off(turn2["turn_id"])
    rt2.work.promote_runnable()
    assert rt2.work_store.get(partial_work.work_id).status == "waiting"


def test_lapse_is_durable_history_and_resolution_clears_live_annotation(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    message = "Make the change within 1 second"
    owner, _cid, _turn, obligation_id = _commit(rt, message, temporal="within 1 second")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert rt.obligation_store.observe_lapses(future) == (obligation_id,)
    assert rt.obligation_store.get(obligation_id).lapsed_at is not None
    _completed_support(rt, owner, obligation_id)
    rt.obligation_reconciler.provider = ScriptProvider()
    assert rt.obligation_reconciler.reconcile_noncommunication() == (obligation_id,)
    resolved = rt.obligation_store.get(obligation_id)
    assert resolved.status == "resolved" and resolved.lapsed_at is None
    assert any(event["kind"] == "lapse_observed" for event in rt.obligation_store.events(obligation_id))


def test_supersession_requires_later_owner_turn(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Supersession")["conversation_id"]
    first = rt.chat_store.append_owner(cid, "Do A and do B", principal_id=owner)
    attempt = rt.obligation_store.begin_attempt(first["turn_id"])
    ids = rt.obligation_store.commit_intake(
        first["turn_id"], [
            {"grounding_excerpt": "Do A", "text": "Do A", "kind": "state_change"},
            {"grounding_excerpt": "do B", "text": "Do B", "kind": "state_change"},
        ], attempts=attempt, provider="test", model="test",
    ).obligation_ids
    with pytest.raises(ValueError, match="later owner turn"):
        rt.obligation_store.supersede(ids[0], ids[1])
    later = rt.chat_store.append_owner(cid, "Replace A with C", principal_id=owner)
    attempt = rt.obligation_store.begin_attempt(later["turn_id"])
    replacement = rt.obligation_store.commit_intake(
        later["turn_id"],
        [{"grounding_excerpt": "Replace A with C", "text": "Do C instead", "kind": "state_change"}],
        attempts=attempt, provider="test", model="test",
    ).obligation_ids[0]
    old, new = rt.obligation_store.supersede(ids[0], replacement)
    assert old.status == "superseded" and new.supersedes == ids[0]


def test_stale_unserviceable_surfaces_without_reopening(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    _owner, cid, _turn, obligation_id = _commit(rt, "Do the unavailable thing")
    explanation = rt.chat_store.append(cid, "assistant", "I cannot service that with the current registry.")
    current = rt.obligation_store.get(obligation_id)
    old_fingerprint = "registry-before-change"
    rt.obligation_store.resolve_unserviceable(
        obligation_id, base_revision=current.revision, registry_fingerprint=old_fingerprint,
        search_basis={"query": "unavailable"}, owner_facing_turn_id=explanation["turn_id"],
    )
    assert rt.obligation_store.get(obligation_id).status == "resolved"
    current_fingerprint = registry_fingerprint(rt.capabilities_registry)
    assert current_fingerprint != old_fingerprint
    assert rt.obligation_store.stale_unserviceable(current_fingerprint)[0]["obligation_id"] == obligation_id
    assert any(row["kind"] == "stale_unserviceable" and row["obligation_id"] == obligation_id for row in rt.attention.snapshot())
    assert rt.obligation_store.get(obligation_id).status == "resolved"


def test_withdrawal_is_owner_grounded_and_work_cannot_write_it(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, source_turn, obligation_id = _commit(rt, "Do this task")
    _register_effect(rt)
    work = rt.work.create("Mechanism", [_mapped_step("test.effect", obligation_id)], owner_principal_id=owner)
    rt.work.cancel(work.work_id)
    assert rt.obligation_store.get(obligation_id).status == "open"
    with pytest.raises(ValueError, match="later owner turn"):
        rt.obligation_store.withdraw(
            obligation_id, actor_turn_id=source_turn["turn_id"], grounding_excerpt="Do this task"
        )
    later = rt.chat_store.append_owner(cid, "Cancel that task", principal_id=owner)
    withdrawn = rt.obligation_store.withdraw(
        obligation_id, actor_turn_id=later["turn_id"], grounding_excerpt="Cancel that task"
    )
    assert withdrawn.status == "withdrawn" and withdrawn.resolution_ref == f"chat_turn:{later['turn_id']}"


def test_deleting_every_binding_never_changes_obligation_truth(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, turn, obligation_id = _commit(rt, "Stage this responsibility")
    _register_effect(rt)
    work = rt.work.create(
        "Stage", [_mapped_step("test.effect", obligation_id)], owner_principal_id=owner,
        metadata={"chat_origin": {"conversation_id": cid, "owner_turn_id": turn["turn_id"]}}, stage=True,
    )
    before = rt.obligation_store.get(obligation_id)
    with rt.work_store._db() as db:
        db.execute("DELETE FROM obligation_bindings WHERE work_id=?", (work.work_id,))
    after = rt.obligation_store.get(obligation_id)
    assert (after.status, after.resolution_kind, after.resolution_ref, after.revision) == (
        before.status, before.resolution_kind, before.resolution_ref, before.revision
    )
    violations = collect_runtime_violations(
        rt.chat_store, rt.obligation_store, rt.work_store, rt.actions_store, rt.evidence
    )
    assert any(item.code == "staged_work_unbacked" and item.reference == work.work_id for item in violations)


def test_owner_turn_schema_is_fail_closed_and_rejects_invalid_intake_state(tmp_path):
    chat, _obligations, cid = _stores(tmp_path)
    turn = chat.append_owner(cid, "Do something", principal_id="owner-1")
    assert turn["intake_status"] == "failed" and turn["intake_error_code"] == "intake_not_completed"
    with sqlite3.connect(chat.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO chat_turns(turn_id,conversation_id,role,content,owner_principal_id,intake_status,intake_schema_version)
                   VALUES ('bad-null',?,'user','x','owner-1',NULL,1)""", (cid,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO chat_turns(turn_id,conversation_id,role,content,owner_principal_id,intake_status,intake_schema_version)
                   VALUES ('bad-value',?,'user','x','owner-1','pending',1)""", (cid,)
            )


def test_staged_work_without_step_level_backing_is_rejected(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    _register_effect(rt)
    with pytest.raises(ValueError, match="step-level obligation binding"):
        rt.work.create(
            "Invalid staged Work", [{"capability_id": "test.effect", "input": {}}],
            owner_principal_id=owner,
            metadata={"chat_origin": {"conversation_id": "c", "owner_turn_id": "t"}}, stage=True,
        )


def test_startup_refuses_invalid_owner_intake_and_unbacked_staged_work(tmp_path):
    root = tmp_path / "bad-owner"
    rt = build_runtime(root)
    _owner, _cid, turn, _obligation_id = _commit(rt, "Keep this valid first")
    with sqlite3.connect(root / "atlas-chat.db") as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute("UPDATE chat_turns SET intake_status=NULL WHERE turn_id=?", (turn["turn_id"],))
    with pytest.raises(RuntimeInvariantError, match="invalid_owner_intake"):
        build_runtime(root)

    root2 = tmp_path / "bad-work"
    rt = build_runtime(root2)
    owner, cid, turn, obligation_id = _commit(rt, "Stage safely")
    _register_effect(rt)
    work = rt.work.create(
        "Stage safely", [_mapped_step("test.effect", obligation_id)], owner_principal_id=owner,
        metadata={"chat_origin": {"conversation_id": cid, "owner_turn_id": turn["turn_id"]}}, stage=True,
    )
    with rt.work_store._db() as db:
        db.execute("DELETE FROM obligation_bindings WHERE work_id=?", (work.work_id,))
    with pytest.raises(RuntimeInvariantError, match="staged_work_unbacked"):
        build_runtime(root2)


def test_direct_consequential_action_binds_exact_occurrence_before_execution(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    _register_effect(rt)
    cid = rt.chat_store.create_conversation("Direct")["conversation_id"]
    message = "Apply the change now"
    intake = json.dumps({
        "obligations": [{"grounding_excerpt": message, "text": message, "kind": "state_change"}],
        "unmapped_spans": [],
    })

    def direct_plan(request):
        obligation_id = json.loads(request.input)["owner_obligations"][0]["obligation_id"]
        return json.dumps({
            "kind": "capability", "capability_id": "test.effect", "input": {},
            "obligation_ids": [obligation_id],
        })

    provider = ScriptProvider(
        intake=intake,
        planner=[direct_plan, '{"kind":"reply","reply":"The change was dispatched and recorded."}'],
        state_verify='{"fulfilled":true,"reason":"The successful effect receipt proves the change."}',
    )
    rt.chat.provider = provider; rt.obligation_intake.provider = provider
    result = rt.chat.send(cid, message, principal_id=owner, defer_capture=True)
    obligation = rt.obligation_store.for_turn(result["_owner_turn_id"])[0]
    servicing = rt.work_store.servicing(obligation.obligation_id)
    assert len(servicing) == 1 and servicing[0]["mechanism_kind"] == "occurrence"
    occurrence = rt.actions_store.get(servicing[0]["mechanism_id"])
    assert occurrence.status == "succeeded"
    rt.obligation_reconciler.provider = provider
    assert rt.obligation_reconciler.reconcile_noncommunication() == (obligation.obligation_id,)
    assert rt.obligation_store.get(obligation.obligation_id).status == "resolved"


def test_direct_verified_reply_atomically_fulfils_communication_obligation(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Direct communication")["conversation_id"]
    message = "Give me a short greeting"
    intake = json.dumps({
        "obligations": [{"grounding_excerpt": message, "text": message, "kind": "communication"}],
        "unmapped_spans": [],
    })

    def verify(request):
        basis = json.loads(request.input)
        oid = basis["communication_obligations"][0]["obligation_id"]
        return json.dumps({"grounded": True, "fulfilled_obligation_ids": [oid], "unsupported_claims": []})

    provider = ScriptProvider(
        intake=intake,
        planner=['{"kind":"reply","reply":"Hello from Atlas."}'],
        communication_verify=verify,
    )
    rt.chat.provider = provider; rt.obligation_intake.provider = provider
    result = rt.chat.send(cid, message, principal_id=owner, defer_capture=True)
    obligation = rt.obligation_store.for_turn(result["_owner_turn_id"])[0]
    assert obligation.status == "resolved"
    assert obligation.resolution_ref == f"chat_turn:{result['turn']['turn_id']}"
    assert result["turn"]["metadata"]["communication_delivery"]["grounded"] is True


def test_forbidden_state_list_has_one_executable_check_per_bullet():
    import atlas_core.obligations.invariants as invariants_module

    source = inspect.getsource(invariants_module.collect_runtime_violations)
    tree = ast.parse(source)
    implemented = {
        call.args[1].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_append"
        and len(call.args) > 1
        and isinstance(call.args[1], ast.Constant)
        and isinstance(call.args[1].value, str)
    }
    frozen = set(invariants_module.FORBIDDEN_STATE_CHECK_IDS)
    assert implemented == frozen
    assert len(invariants_module.FORBIDDEN_STATE_CHECK_IDS) == 21


def test_flagship_restart_health_then_cullinan_weather_survives_restart_without_second_owner_turn(tmp_path, monkeypatch):
    """Frozen flagship: one owner turn, three obligations, detached restart, verified report."""
    monkeypatch.setenv("ATLAS_SERVICE_UNIT", "atlas-api.service")
    monkeypatch.setenv("INVOCATION_ID", "old-invocation")

    def fake_systemd(args, timeout=20):
        if "show" in args:
            stdout = (
                "Id=atlas-api.service\nLoadState=loaded\nActiveState=active\nSubState=running\n"
                "MainPID=222\nInvocationID=new-invocation\nExecMainStatus=0\n"
            )
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(host_module, "_run", fake_systemd)
    root = tmp_path / "instance"
    rt = build_runtime(root)
    owner = rt.identities.current_owner().principal_id

    def register_weather(runtime):
        runtime.capabilities_registry.register(CapabilityRegistration(
            CapabilityDefinition(
                "test.weather.read", "Read deterministic Cullinan weather", "read", "none",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            lambda payload: ScopeResolution("test/weather/cullinan", {}, "Read Cullinan weather"),
            lambda payload: ActionResult(
                True,
                {"location": "Cullinan", "temperature_c": 24, "condition": "clear"},
                {"ok": True, "observed_at": "2026-09-05T08:00:00+00:00"},
            ),
        ), replace=True)
        runtime.policy_store.set(
            principal_id=owner, scope="test/weather/cullinan", operation="read", decision="YES"
        )

    register_weather(rt)
    cid = rt.chat_store.create_conversation("Flagship restart")["conversation_id"]
    message = (
        "Restart your API service, then verify that it came back healthy. "
        "Then tell me what the weather is like today in Cullinan."
    )

    class FlagshipChatProvider:
        def __init__(self):
            self.requests = []
            self.planner_round = 0

        def generate(self, request):
            self.requests.append(request)
            if request.capability_id == "chat.obligation_intake":
                text = json.dumps({
                    "obligations": [
                        {"grounding_excerpt": "Restart your API service", "text": "Restart the API service", "kind": "state_change"},
                        {"grounding_excerpt": "verify that it came back healthy", "text": "Verify the API is healthy", "kind": "state_change"},
                        {"grounding_excerpt": "tell me what the weather is like today in Cullinan", "text": "Report today's Cullinan weather", "kind": "communication"},
                    ],
                    "unmapped_spans": [],
                })
            elif request.capability_id == "chat.communication_delivery_verify":
                text = '{"grounded":false,"fulfilled_obligation_ids":[],"unsupported_claims":[]}'
            else:
                self.planner_round += 1
                if self.planner_round == 1:
                    obligations = json.loads(request.input)["owner_obligations"]
                    by_text = {row["text"]: row["obligation_id"] for row in obligations}
                    text = json.dumps({
                        "kind": "capability", "capability_id": "work.create", "input": {
                            "objective": "Restart Atlas, verify health, then gather Cullinan weather",
                            "run": True,
                            "steps": [
                                {
                                    "capability_id": "host.service.restart",
                                    "description": "Restart Atlas API",
                                    "input": {"unit": "atlas-api.service"},
                                    "obligation_ids": [by_text["Restart the API service"]],
                                },
                                {
                                    "capability_id": "host.service.status",
                                    "description": "Verify Atlas API health",
                                    "input": {"unit": "atlas-api.service"},
                                    "obligation_ids": [by_text["Verify the API is healthy"]],
                                },
                                {
                                    "capability_id": "test.weather.read",
                                    "description": "Read today's Cullinan weather",
                                    "input": {},
                                    "obligation_ids": [by_text["Report today's Cullinan weather"]],
                                },
                            ],
                        },
                    })
                else:
                    text = '{"kind":"reply","reply":"I staged the ordered responsibility and will continue it after this response is handed off."}'
            return ModelResponse(text=text, provider_key="test", model="flagship-chat", raw={})

    provider = FlagshipChatProvider()
    rt.chat.provider = provider
    rt.obligation_intake.provider = provider
    initial = rt.chat.send(cid, message, principal_id=owner, defer_capture=True)
    owner_turn = rt.chat_store.turn(initial["_owner_turn_id"])
    obligations = rt.obligation_store.for_turn(owner_turn["turn_id"])
    assert len(obligations) == 3
    assert [item.kind for item in obligations] == ["state_change", "state_change", "communication"]
    work = rt.work_store.list(limit=10)[0]
    assert work.status == "staged"
    assert not [row for row in rt.actions_store.recent(limit=50) if row.capability_id == "host.service.restart"]

    rt.chat_store.mark_response_handed_off(owner_turn["turn_id"])
    assert rt.work.promote_runnable() == (work.work_id,)
    waiting = rt.work.run_runnable()[0]
    assert waiting["status"] == "waiting"
    assert [step["status"] for step in waiting["steps"]] == ["waiting", "queued", "queued"]
    restart_occurrence = rt.actions_store.get(waiting["steps"][0]["occurrence_id"])
    assert restart_occurrence.status == "uncertain"

    monkeypatch.setenv("INVOCATION_ID", "new-invocation")
    rt2 = build_runtime(root)
    register_weather(rt2)
    recovered = rt2.work.detail(work.work_id)
    assert [step["status"] for step in recovered["steps"]] == ["completed", "queued", "queued"]
    assert rt2.work.promote_runnable() == (work.work_id,)
    completed = rt2.work.run_runnable()[0]
    assert completed["status"] == "completed"
    assert [step["status"] for step in completed["steps"]] == ["completed", "completed", "completed"]

    class FlagshipReconcileProvider:
        def generate(self, request):
            if request.capability_id == "obligation.state_change_verify":
                text = '{"fulfilled":true,"reason":"The explicitly bound successful runtime evidence proves this exact obligation."}'
            elif request.capability_id == "chat.obligation_report":
                text = '{"kind":"reply","reply":"Atlas restarted and verified healthy. Today in Cullinan it is 24°C and clear."}'
            elif request.capability_id == "chat.obligation_report_verify":
                text = '{"grounded":true,"unsupported_claims":[]}'
            else:
                raise AssertionError(f"unexpected reconciliation provider role: {request.capability_id}")
            return ModelResponse(text=text, provider_key="test", model="flagship-reconcile", raw={})

    rt2.obligation_reconciler.provider = FlagshipReconcileProvider()
    tick = rt2.obligation_reconciler.tick()
    assert len(tick["resolved"]) == 2
    assert len(tick["reports"]) == 1
    final = [rt2.obligation_store.get(item.obligation_id) for item in obligations]
    assert [item.status for item in final] == ["resolved", "resolved", "resolved"]
    assert final[0].resolution_ref.startswith("evidence:")
    assert final[1].resolution_ref.startswith("evidence:")
    assert final[2].resolution_ref == f"chat_turn:{tick['reports'][0]}"
    report_turn = rt2.chat_store.turn(tick["reports"][0])
    assert "Cullinan" in report_turn["content"] and "24°C" in report_turn["content"]
    owner_turns = [turn for turn in rt2.chat_store.turns(cid) if turn["role"] == "user"]
    assert [turn["turn_id"] for turn in owner_turns] == [owner_turn["turn_id"]]

    # No persisted report ever described a serviced commitment as outstanding.
    reports = [turn for turn in rt2.chat_store.turns(cid)
               if turn["role"] == "assistant" and turn["metadata"].get("obligation_report")]
    assert len(reports) == 1
    report_meta = reports[0]["metadata"]["obligation_report"]
    assert report_meta["owner_turn_id"] == owner_turn["turn_id"]
    assert report_meta["outstanding_obligation_revisions"] == {}
    assert report_meta["outstanding_obligation_states"] == {}
    assert collect_runtime_violations(
        rt2.chat_store, rt2.obligation_store, rt2.work_store, rt2.actions_store, rt2.evidence
    ) == ()
