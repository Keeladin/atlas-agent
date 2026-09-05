from __future__ import annotations

import json
import sqlite3

import pytest

from atlas_api.compose import build_runtime
from atlas_core.chat import ChatStore
from atlas_core.obligations import ObligationIntakeRuntime, ObligationStore
from atlas_core.providers import ModelResponse


class RoleProvider:
    def __init__(self, intake: str, planner: str = '{"kind":"reply","reply":"Done."}') -> None:
        self.intake = intake
        self.planner = planner
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if request.capability_id == "chat.obligation_intake":
            text = self.intake
        elif request.capability_id == "chat.communication_delivery_verify":
            text = '{"grounded":false,"fulfilled_obligation_ids":[],"unsupported_claims":[]}'
        else:
            text = self.planner
        return ModelResponse(text=text, provider_key="test", model="test-model", raw={})


def _stores(tmp_path):
    path = tmp_path / "chat.db"
    chat = ChatStore(path); chat.initialize()
    obligations = ObligationStore(path); obligations.initialize()
    cid = chat.create_conversation("Ledger")["conversation_id"]
    return chat, obligations, cid


def test_authenticated_owner_turn_is_durable_pending_before_intake(tmp_path):
    chat, obligations, cid = _stores(tmp_path)
    turn = chat.append_owner(cid, "Do the thing", principal_id="owner-1")
    assert turn["intake_status"] == "pending"
    assert turn["owner_principal_id"] == "owner-1"
    attention = obligations.intake_attention()
    assert [row["turn_id"] for row in attention] == [turn["turn_id"]]


def test_boot_converts_only_persisted_pending_intake_to_interrupted(tmp_path):
    chat, obligations, cid = _stores(tmp_path)
    pending = chat.append_owner(cid, "Do the thing", principal_id="owner-1")
    complete = chat.append_owner(cid, "hi", principal_id="owner-1")
    attempt = obligations.begin_attempt(complete["turn_id"])
    obligations.commit_intake(
        complete["turn_id"], [], attempts=attempt, provider="test", model="test"
    )
    changed = chat.interrupt_pending_intakes()
    assert changed == (pending["turn_id"],)
    assert chat.turn(pending["turn_id"])["intake_status"] == "interrupted"
    assert chat.turn(complete["turn_id"])["intake_status"] == "complete"


def test_interrupted_intake_is_retryable_without_partial_commitments(tmp_path):
    chat, obligations, cid = _stores(tmp_path)
    turn = chat.append_owner(cid, "Restart Atlas", principal_id="owner-1")
    chat.interrupt_pending_intakes()
    attempt = obligations.begin_attempt(turn["turn_id"])
    result = obligations.commit_intake(
        turn["turn_id"],
        [{"grounding_excerpt": "Restart Atlas", "text": "Restart Atlas", "kind": "state_change"}],
        attempts=attempt, provider="test", model="test",
    )
    assert result.status == "complete"
    assert len(obligations.for_turn(turn["turn_id"])) == 1


def test_zero_obligation_greeting_is_complete_not_failed(tmp_path):
    chat, obligations, cid = _stores(tmp_path)
    turn = chat.append_owner(cid, "hi", principal_id="owner-1")
    provider = RoleProvider('{"obligations":[],"unmapped_spans":[]}')
    result = ObligationIntakeRuntime(obligations, provider).capture(turn)
    assert result.status == "complete"
    assert result.obligation_ids == ()
    assert obligations.for_turn(turn["turn_id"]) == ()
    assert obligations.intake_attention() == ()


def test_partial_intake_keeps_captured_commitment_and_turn_visible(tmp_path):
    chat, obligations, cid = _stores(tmp_path)
    message = "Restart Atlas, but only after the report is saved"
    turn = chat.append_owner(cid, message, principal_id="owner-1")
    provider = RoleProvider(json.dumps({
        "obligations": [{
            "grounding_excerpt": "Restart Atlas", "text": "Restart Atlas", "kind": "state_change"
        }],
        "unmapped_spans": ["but only after the report is saved"],
    }))
    result = ObligationIntakeRuntime(obligations, provider).capture(turn)
    assert result.status == "partial"
    assert len(obligations.for_turn(turn["turn_id"])) == 1
    attention = obligations.intake_attention()
    assert attention[0]["intake_status"] == "partial"
    assert attention[0]["unmapped_spans"] == ["but only after the report is saved"]


def test_invalid_grounding_never_commits_an_obligation(tmp_path):
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


def test_three_outcome_turn_is_enumerated_before_planning(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Three outcomes")["conversation_id"]
    message = (
        "Restart your API service, then verify that it came back healthy. "
        "Then tell me what the weather is like today in Cullinan."
    )
    provider = RoleProvider(json.dumps({
        "obligations": [
            {"grounding_excerpt": "Restart your API service", "text": "Restart the API service", "kind": "state_change"},
            {"grounding_excerpt": "verify that it came back healthy", "text": "Verify the API is healthy", "kind": "state_change"},
            {"grounding_excerpt": "tell me what the weather is like today in Cullinan", "text": "Report today's Cullinan weather", "kind": "communication"},
        ],
        "unmapped_spans": [],
    }), planner='{"kind":"reply","reply":"I have the three commitments enumerated."}')
    rt.chat.provider = provider
    rt.obligation_intake.provider = provider
    result = rt.chat.send(cid, message, principal_id=owner.principal_id, defer_capture=True)
    owner_turn = next(t for t in rt.chat_store.turns(cid) if t["role"] == "user")
    rows = rt.obligation_store.for_turn(owner_turn["turn_id"])
    assert [row.kind for row in rows] == ["state_change", "state_change", "communication"]
    assert owner_turn["intake_status"] == "complete"
    assert provider.requests[0].capability_id == "chat.obligation_intake"
    intake_input = json.loads(provider.requests[0].input)
    assert set(intake_input) == {"owner_message", "recent_conversation"}
    assert "capability" not in provider.requests[0].input.casefold()
    assert provider.requests[1].capability_id != "chat.obligation_intake"
    assert result["turn"]["content"] == "I have the three commitments enumerated."


def test_exhausted_intake_failure_never_reaches_planning_or_execution(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Failed intake")["conversation_id"]
    provider = RoleProvider("not-json")
    rt.chat.provider = provider
    rt.obligation_intake.provider = provider
    result = rt.chat.send(cid, "Restart Atlas", principal_id=owner.principal_id)
    owner_turn = next(t for t in rt.chat_store.turns(cid) if t["role"] == "user")
    assert owner_turn["intake_status"] == "failed"
    assert owner_turn["intake_attempts"] == 2
    assert owner_turn["intake_error_code"] == "intake_unparseable_response"
    assert rt.obligation_store.for_turn(owner_turn["turn_id"]) == ()
    assert len(provider.requests) == 2
    assert all(req.capability_id == "chat.obligation_intake" for req in provider.requests)
    assert rt.actions_store.recent(limit=20) == ()
    assert "didn't execute anything" in result["turn"]["content"]


def test_owner_turn_schema_rejects_null_or_invalid_intake_state(tmp_path):
    chat, _obligations, cid = _stores(tmp_path)
    with sqlite3.connect(chat.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO chat_turns(
                       turn_id,conversation_id,role,content,owner_principal_id,intake_status,intake_schema_version
                   ) VALUES ('turn_bad',?,'user','x','owner-1',NULL,1)""", (cid,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO chat_turns(
                       turn_id,conversation_id,role,content,owner_principal_id,intake_status,intake_schema_version
                   ) VALUES ('turn_bad2',?,'user','x','owner-1','unknown',1)""", (cid,)
            )


def test_preledger_chat_schema_requires_explicit_development_reset(tmp_path):
    path = tmp_path / "chat.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE chat_turns(turn_id TEXT PRIMARY KEY, role TEXT NOT NULL)")
    with pytest.raises(RuntimeError, match="development schema reset"):
        ChatStore(path).initialize()


def _open_obligation(rt, message="Do the durable thing"):
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Binding")["conversation_id"]
    turn = rt.chat_store.append_owner(cid, message, principal_id=owner)
    attempts = rt.obligation_store.begin_attempt(turn["turn_id"])
    result = rt.obligation_store.commit_intake(
        turn["turn_id"],
        [{"grounding_excerpt": message, "text": message, "kind": "state_change"}],
        attempts=attempts, provider="test", model="test",
    )
    return owner, cid, turn, result.obligation_ids[0]


def test_staged_work_requires_and_retains_database_backing(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, turn, obligation_id = _open_obligation(rt)
    work = rt.work.create(
        "Service obligation", [{"capability_id":"memory.search","input":{"query":"x"}}],
        owner_principal_id=owner,
        metadata={"chat_origin":{"conversation_id":cid,"owner_turn_id":turn["turn_id"]}},
        obligation_ids=[obligation_id], stage=True,
    )
    assert work.status == "staged"
    bindings = rt.work_store.bindings(work.work_id)
    assert [row["obligation_id"] for row in bindings] == [obligation_id]
    with pytest.raises(sqlite3.IntegrityError, match="final backing obligation"):
        with rt.work_store._db() as db:
            db.execute("DELETE FROM obligation_bindings WHERE binding_id=?", (bindings[0]["binding_id"],))
    assert rt.obligation_store.get(obligation_id).status == "open"


def test_unbacked_work_cannot_transition_to_staged(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    work = rt.work_store.create(
        "Unbacked", owner, [{"capability_id":"memory.search","input":{"query":"x"}}]
    )
    with pytest.raises(sqlite3.IntegrityError, match="backing obligation"):
        rt.work_store.set_work_status(work.work_id, "staged")


def test_binding_is_authoritative_for_servicing_not_resolution(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, turn, obligation_id = _open_obligation(rt)
    work = rt.work.create(
        "Service obligation", [{"capability_id":"memory.search","input":{"query":"x"}}],
        owner_principal_id=owner,
        metadata={"chat_origin":{"conversation_id":cid,"owner_turn_id":turn["turn_id"]}},
        obligation_ids=[obligation_id], stage=True,
    )
    rt.work_store.set_work_status(work.work_id, "queued")
    with rt.work_store._db() as db:
        db.execute("DELETE FROM obligation_bindings WHERE work_id=?", (work.work_id,))
    assert rt.work_store.bindings(work.work_id) == ()
    assert rt.obligation_store.get(obligation_id).status == "open"


def test_runtime_refuses_staged_work_with_nonexistent_obligation(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    with pytest.raises(KeyError):
        rt.work.create(
            "Invalid backing", [{"capability_id":"memory.search","input":{"query":"x"}}],
            owner_principal_id=owner, obligation_ids=["obligation_missing"], stage=True,
        )


def test_attention_unions_incomplete_intake_and_unserviced_obligations(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, turn, obligation_id = _open_obligation(rt, "Inspect Atlas")
    pending = rt.chat_store.append_owner(cid, "And tell me the result", principal_id=owner)
    snapshot = rt.attention.snapshot()
    assert any(row["kind"] == "incomplete_intake" and row["owner_turn_id"] == pending["turn_id"] for row in snapshot)
    assert any(row["kind"] == "unserviced_obligation" and row["obligation_id"] == obligation_id for row in snapshot)

    work = rt.work.create(
        "Inspect Atlas", [{"capability_id":"memory.search","input":{"query":"atlas"}}],
        owner_principal_id=owner,
        metadata={"chat_origin":{"conversation_id":cid,"owner_turn_id":turn["turn_id"]}},
        obligation_ids=[obligation_id], stage=True,
    )
    snapshot = rt.attention.snapshot()
    assert not any(row["kind"] == "unserviced_obligation" and row["obligation_id"] == obligation_id for row in snapshot)
    assert rt.work_store.bindings(work.work_id)


class ScriptProvider:
    def __init__(self, intake: str, *planner: str) -> None:
        self.intake = intake
        self.planner = list(planner)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if request.capability_id == "chat.obligation_intake":
            text = self.intake
        else:
            if not self.planner:
                raise AssertionError(f"unexpected provider call: {request.capability_id}")
            text = self.planner.pop(0)
        return ModelResponse(text=text, provider_key="test", model="test-model", raw={})


def test_chat_work_stays_detached_until_response_handoff(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Detached")["conversation_id"]
    message = "Search durable memory for Atlas"
    intake = json.dumps({"obligations":[{
        "grounding_excerpt": message, "text": message, "kind":"state_change"
    }],"unmapped_spans":[]})
    provider = ScriptProvider(
        intake,
        json.dumps({"kind":"capability","capability_id":"work.create","input":{
            "objective":"Search durable memory",
            "steps":[{"capability_id":"memory.search","input":{"query":"Atlas"}}],
            "run":True,
        }}),
        '{"kind":"reply","reply":"The work is staged."}',
    )
    rt.chat.provider = provider
    rt.obligation_intake.provider = provider
    result = rt.chat.send(cid, message, principal_id=owner.principal_id, defer_capture=True)
    owner_turn = rt.chat_store.turn(result["_owner_turn_id"])
    work = rt.work_store.list(limit=10)[0]
    assert work.status == "staged"
    assert owner_turn["turn_completed_at"] is not None
    assert owner_turn["response_handed_off_at"] is None
    assert not [row for row in rt.actions_store.recent(limit=20) if row.capability_id == "memory.search"]

    rt.work.promote_runnable()
    assert rt.work_store.get(work.work_id).status == "waiting"
    assert not [row for row in rt.actions_store.recent(limit=20) if row.capability_id == "memory.search"]

    rt.chat_store.mark_response_handed_off(owner_turn["turn_id"])
    assert rt.work.promote_runnable() == (work.work_id,)
    assert rt.work_store.get(work.work_id).status == "runnable"
    details = rt.work.run_runnable()
    assert details[0]["status"] == "completed"
    assert [row for row in rt.actions_store.recent(limit=20) if row.capability_id == "memory.search"]


def _commit_obligation(rt, message: str, *, kind: str, satisfiable_until: str | None = None):
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Reconcile")["conversation_id"]
    turn = rt.chat_store.append_owner(cid, message, principal_id=owner)
    attempts = rt.obligation_store.begin_attempt(turn["turn_id"])
    item = {"grounding_excerpt": message, "text": message, "kind": kind}
    if satisfiable_until is not None:
        item["satisfiable_until"] = satisfiable_until
    result = rt.obligation_store.commit_intake(
        turn["turn_id"], [item], attempts=attempts, provider="test", model="test"
    )
    return owner, cid, turn, result.obligation_ids[0]


def _completed_bound_search(rt, owner: str, obligation_id: str, query: str = "x"):
    work = rt.work.create(
        "Gather supporting evidence",
        [{"capability_id": "memory.search", "input": {"query": query}}],
        owner_principal_id=owner, obligation_ids=[obligation_id],
    )
    detail = rt.work.run(work.work_id)
    assert detail["status"] == "completed"
    return work


def test_state_change_never_resolves_from_work_status_without_action_evidence(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, _turn, obligation_id = _commit_obligation(
        rt, "Make the durable change", kind="state_change"
    )
    work = rt.work_store.create(
        "Fake completion", owner,
        [{"capability_id": "memory.search", "input": {"query": "x"}}],
        obligation_ids=[obligation_id],
    )
    step = rt.work_store.steps(work.work_id)[0]
    rt.work_store.set_step(step.step_id, status="completed", output={"fake": True})
    rt.work_store.set_work_status(work.work_id, "completed")
    assert rt.obligation_reconciler.reconcile_noncommunication() == ()
    assert rt.obligation_store.get(obligation_id).status == "open"

    _completed_bound_search(rt, owner, obligation_id, "real evidence")
    assert rt.obligation_reconciler.reconcile_noncommunication() == (obligation_id,)
    resolved = rt.obligation_store.get(obligation_id)
    assert resolved.status == "resolved"
    assert resolved.resolution_kind == "fulfilled"
    assert resolved.resolution_ref.startswith("evidence:")


def test_policy_no_resolves_with_authoritative_action_reference(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, _turn, obligation_id = _commit_obligation(
        rt, "Search memory as requested", kind="state_change"
    )
    rt.policy_store.set(
        principal_id=owner, scope="atlas/memory", operation="search", decision="NO"
    )
    work = rt.work.create(
        "Policy refused service",
        [{"capability_id": "memory.search", "input": {"query": "x"}}],
        owner_principal_id=owner, obligation_ids=[obligation_id],
    )
    rt.work.run(work.work_id)
    changed = rt.obligation_reconciler.reconcile_noncommunication()
    assert changed == (obligation_id,)
    resolved = rt.obligation_store.get(obligation_id)
    assert resolved.status == "resolved"
    assert resolved.resolution_kind == "declined_policy"
    assert resolved.resolution_ref.startswith("action:")


def test_work_cancellation_leaves_obligation_open_and_attention_visible(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, _turn, obligation_id = _commit_obligation(
        rt, "Keep responsibility for this", kind="state_change"
    )
    work = rt.work.create(
        "Disposable mechanism", [{"capability_id": "memory.search", "input": {"query": "x"}}],
        owner_principal_id=owner, obligation_ids=[obligation_id],
    )
    rt.work.cancel(work.work_id)
    rt.obligation_reconciler.reconcile_noncommunication()
    assert rt.obligation_store.get(obligation_id).status == "open"
    assert any(
        row["kind"] == "unserviced_obligation" and row["obligation_id"] == obligation_id
        for row in rt.attention.snapshot()
    )


def test_unserviced_obligation_survives_runtime_restart(tmp_path):
    root = tmp_path / "instance"
    rt = build_runtime(root)
    _owner, _cid, _turn, obligation_id = _commit_obligation(
        rt, "Remember this dangling duty", kind="state_change"
    )
    rt2 = build_runtime(root)
    assert rt2.obligation_store.get(obligation_id).status == "open"
    assert any(
        row["kind"] == "unserviced_obligation" and row["obligation_id"] == obligation_id
        for row in rt2.attention.snapshot()
    )


class ReportProvider:
    def __init__(self, report: str, verification: str) -> None:
        self.responses = [report, verification]
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected provider call: {request.capability_id}")
        return ModelResponse(
            text=self.responses.pop(0), provider_key="test", model="report-model", raw={}
        )


def test_communication_stays_open_until_verified_chat_turn_is_persisted(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, _turn, obligation_id = _commit_obligation(
        rt, "Tell me the recorded result", kind="communication"
    )
    _completed_bound_search(rt, owner, obligation_id, "result")
    assert rt.obligation_store.get(obligation_id).status == "open"
    assert rt.obligation_reconciler.reconcile_noncommunication() == ()

    provider = ReportProvider(
        '{"kind":"reply","reply":"The recorded search completed successfully."}',
        '{"grounded":true,"unsupported_claims":[]}',
    )
    rt.obligation_reconciler.provider = provider
    reports = rt.obligation_reconciler.report_communications()
    assert len(reports) == 1
    resolved = rt.obligation_store.get(obligation_id)
    assert resolved.status == "resolved"
    assert resolved.resolution_kind == "fulfilled"
    assert resolved.resolution_ref == f"chat_turn:{reports[0]}"
    persisted = rt.chat_store.turn(reports[0])
    assert persisted["conversation_id"] == cid
    assert persisted["metadata"]["obligation_report"]["verification"]["grounded"] is True


def test_failed_report_verification_leaves_communication_open(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, _turn, obligation_id = _commit_obligation(
        rt, "Tell me the verified result", kind="communication"
    )
    _completed_bound_search(rt, owner, obligation_id, "result")
    rt.obligation_reconciler.provider = ReportProvider(
        '{"kind":"reply","reply":"An unsupported result."}',
        '{"grounded":false,"unsupported_claims":["unsupported result"]}',
    )
    assert rt.obligation_reconciler.report_communications() == ()
    assert rt.obligation_store.get(obligation_id).status == "open"


def test_report_is_discarded_when_obligation_revision_changes_before_persistence(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, _turn, obligation_id = _commit_obligation(
        rt, "Tell me before the deadline", kind="communication",
        satisfiable_until="2000-01-01T00:00:00+00:00",
    )
    _completed_bound_search(rt, owner, obligation_id, "deadline result")

    class RacingProvider:
        def __init__(self):
            self.calls = 0
        def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                text = '{"kind":"reply","reply":"The result is ready."}'
            else:
                rt.obligation_store.observe_lapses("2026-09-05T12:00:00+00:00")
                text = '{"grounded":true,"unsupported_claims":[]}'
            return ModelResponse(text=text, provider_key="test", model="race", raw={})

    rt.obligation_reconciler.provider = RacingProvider()
    assert rt.obligation_reconciler.report_communications() == ()
    current = rt.obligation_store.get(obligation_id)
    assert current.status == "open"
    assert current.lapsed_at is not None
    assert current.revision == 2


def test_lapse_is_event_history_and_resolution_clears_live_annotation(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, _cid, _turn, obligation_id = _commit_obligation(
        rt, "Complete this before yesterday", kind="state_change",
        satisfiable_until="2000-01-01T00:00:00+00:00",
    )
    assert rt.obligation_store.observe_lapses("2026-09-05T12:00:00+00:00") == (obligation_id,)
    lapsed = rt.obligation_store.get(obligation_id)
    assert lapsed.status == "open" and lapsed.lapsed_at is not None
    _completed_bound_search(rt, owner, obligation_id, "late evidence")
    rt.obligation_reconciler.reconcile_noncommunication()
    resolved = rt.obligation_store.get(obligation_id)
    assert resolved.status == "resolved" and resolved.lapsed_at is None
    assert any(event["kind"] == "lapse_observed" for event in rt.obligation_store.events(obligation_id))


def test_supersession_requires_a_later_owner_turn(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    cid = rt.chat_store.create_conversation("Supersede")["conversation_id"]
    first_turn = rt.chat_store.append_owner(cid, "Do A and do B", principal_id=owner)
    attempt = rt.obligation_store.begin_attempt(first_turn["turn_id"])
    result = rt.obligation_store.commit_intake(
        first_turn["turn_id"], [
            {"grounding_excerpt": "Do A", "text": "Do A", "kind": "state_change"},
            {"grounding_excerpt": "do B", "text": "Do B", "kind": "state_change"},
        ], attempts=attempt, provider="test", model="test",
    )
    with pytest.raises(ValueError, match="later owner turn"):
        rt.obligation_store.supersede(result.obligation_ids[0], result.obligation_ids[1])

    later = rt.chat_store.append_owner(cid, "Replace A with C", principal_id=owner)
    attempt = rt.obligation_store.begin_attempt(later["turn_id"])
    replacement = rt.obligation_store.commit_intake(
        later["turn_id"],
        [{"grounding_excerpt": "Replace A with C", "text": "Do C instead", "kind": "state_change"}],
        attempts=attempt, provider="test", model="test",
    ).obligation_ids[0]
    old, new = rt.obligation_store.supersede(result.obligation_ids[0], replacement)
    assert old.status == "superseded"
    assert old.resolution_ref == f"obligation:{replacement}"
    assert new.supersedes == result.obligation_ids[0]


def test_withdrawal_is_owner_grounded_and_cannot_be_written_by_work(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner, cid, source_turn, obligation_id = _commit_obligation(
        rt, "Do this task", kind="state_change"
    )
    work = rt.work.create(
        "Mechanism", [{"capability_id": "memory.search", "input": {"query": "x"}}],
        owner_principal_id=owner, obligation_ids=[obligation_id],
    )
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
    assert withdrawn.status == "withdrawn"
    assert withdrawn.resolution_ref == f"chat_turn:{later['turn_id']}"


def test_unserviceable_registry_drift_surfaces_without_reopening(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    _owner, cid, _turn, obligation_id = _commit_obligation(
        rt, "Do the unavailable thing", kind="state_change"
    )
    explanation = rt.chat_store.append(cid, "assistant", "I cannot service that with the current registry.")
    current = rt.obligation_store.get(obligation_id)
    resolved = rt.obligation_store.resolve_unserviceable(
        obligation_id, base_revision=current.revision,
        registry_fingerprint="registry-old", search_basis={"query": "unavailable thing"},
        owner_facing_turn_id=explanation["turn_id"],
    )
    assert resolved.status == "resolved" and resolved.resolution_kind == "unserviceable"
    assert rt.obligation_store.stale_unserviceable("registry-old") == ()
    stale = rt.obligation_store.stale_unserviceable("registry-new")
    assert stale[0]["obligation_id"] == obligation_id
    assert rt.obligation_store.get(obligation_id).status == "resolved"


def test_startup_refuses_invalid_owner_intake_injected_below_store_api(tmp_path):
    from atlas_core.obligations import RuntimeInvariantError

    root = tmp_path / "instance"
    rt = build_runtime(root)
    owner, _cid, turn, _obligation_id = _commit_obligation(
        rt, "Keep this valid first", kind="state_change"
    )
    assert owner
    with sqlite3.connect(root / "atlas-chat.db") as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute(
            "UPDATE chat_turns SET intake_status=NULL WHERE turn_id=?", (turn["turn_id"],)
        )
    rt2 = build_runtime(root)
    assert rt2.operational.state()["quarantined"] == 1
    assert any(item.code == "invalid_owner_intake" for item in rt2.startup_violations)


def test_startup_refuses_staged_work_injected_without_backing(tmp_path):
    from atlas_core.obligations import RuntimeInvariantError

    root = tmp_path / "instance"
    rt = build_runtime(root)
    owner, cid, turn, obligation_id = _open_obligation(rt, "Stage this safely")
    work = rt.work.create(
        "Stage safely", [{"capability_id": "memory.search", "input": {"query": "x"}}],
        owner_principal_id=owner,
        metadata={"chat_origin": {"conversation_id": cid, "owner_turn_id": turn["turn_id"]}},
        obligation_ids=[obligation_id], stage=True,
    )
    with sqlite3.connect(root / "atlas-work.db") as db:
        db.execute("DROP TRIGGER binding_delete_preserves_staged_work")
        db.execute("DELETE FROM obligation_bindings WHERE work_id=?", (work.work_id,))
    rt2 = build_runtime(root)
    assert rt2.operational.state()["quarantined"] == 1
    assert any(item.code == "staged_work_unbacked" for item in rt2.startup_violations)


class DirectDeliveryProvider:
    def __init__(self, *, grounded: bool) -> None:
        self.grounded = grounded
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if request.capability_id == "chat.obligation_intake":
            message = json.loads(request.input)["owner_message"]
            text = json.dumps({
                "obligations": [{
                    "grounding_excerpt": message, "text": message, "kind": "communication"
                }], "unmapped_spans": [],
            })
        elif request.capability_id == "chat.communication_delivery_verify":
            basis = json.loads(request.input)
            obligation_id = basis["communication_obligations"][0]["obligation_id"]
            text = json.dumps({
                "grounded": self.grounded,
                "fulfilled_obligation_ids": [obligation_id] if self.grounded else [],
                "unsupported_claims": [] if self.grounded else ["not sufficiently grounded"],
            })
        else:
            text = '{"kind":"reply","reply":"Hello from Atlas."}'
        return ModelResponse(text=text, provider_key="test", model="direct", raw={})


def test_direct_verified_reply_atomically_fulfils_communication_obligation(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Direct delivery")["conversation_id"]
    provider = DirectDeliveryProvider(grounded=True)
    rt.chat.provider = provider
    rt.obligation_intake.provider = provider
    result = rt.chat.send(
        cid, "Give me a short greeting", principal_id=owner.principal_id, defer_capture=True
    )
    owner_turn = rt.chat_store.turn(result["_owner_turn_id"])
    obligation = rt.obligation_store.for_turn(owner_turn["turn_id"])[0]
    assert obligation.status == "resolved"
    assert obligation.resolution_ref == f"chat_turn:{result['turn']['turn_id']}"
    assert result["turn"]["content"] == "Hello from Atlas."
    assert result["turn"]["metadata"]["communication_delivery"]["grounded"] is True


def test_direct_unverified_reply_leaves_communication_obligation_open(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner()
    cid = rt.chat_store.create_conversation("Direct unverified")["conversation_id"]
    provider = DirectDeliveryProvider(grounded=False)
    rt.chat.provider = provider
    rt.obligation_intake.provider = provider
    result = rt.chat.send(
        cid, "Give me a short greeting", principal_id=owner.principal_id, defer_capture=True
    )
    obligation = rt.obligation_store.for_turn(result["_owner_turn_id"])[0]
    assert obligation.status == "open"
    assert result["turn"]["metadata"]["communication_delivery"]["grounded"] is False


def test_section_15_forbidden_state_list_has_one_executable_check_per_bullet():
    from atlas_core.obligations.invariants import FORBIDDEN_STATE_CHECK_IDS, mapped_forbidden_state_checks
    assert mapped_forbidden_state_checks() == FORBIDDEN_STATE_CHECK_IDS
    assert len(FORBIDDEN_STATE_CHECK_IDS) == 20
    assert len(set(FORBIDDEN_STATE_CHECK_IDS)) == 20


def test_quarantine_is_sticky_until_repair_and_explicit_clearance(tmp_path):
    from atlas_core.obligations import collect_runtime_violations

    root = tmp_path / "instance"
    rt = build_runtime(root)
    owner, cid, turn, obligation_id = _open_obligation(rt, "Repair this safely")
    work = rt.work.create(
        "Repair safely", [{"capability_id":"memory.search","input":{"query":"x"}}],
        owner_principal_id=owner,
        metadata={"chat_origin":{"conversation_id":cid,"owner_turn_id":turn["turn_id"]}},
        obligation_ids=[obligation_id], stage=True,
    )
    with sqlite3.connect(root / "atlas-work.db") as db:
        db.execute("DROP TRIGGER binding_delete_preserves_staged_work")
        db.execute("DELETE FROM obligation_bindings WHERE work_id=?", (work.work_id,))
    quarantined = build_runtime(root)
    assert quarantined.operational.state()["quarantined"] == 1
    binding = quarantined.work_store.bind_obligation(work.work_id, obligation_id)
    repair_event = quarantined.operational.record_repair(
        runtime_revision=quarantined.runtime_revision, actor=owner,
        reason="restore staged Work backing", evidence={"binding": binding},
    )
    assert repair_event
    current = collect_runtime_violations(
        quarantined.chat_store, quarantined.obligation_store, quarantined.work_store,
        quarantined.actions_store, quarantined.evidence,
    )
    assert current == ()
    assert quarantined.operational.state()["quarantined"] == 1

    clean_boot = build_runtime(root)
    assert clean_boot.startup_violations == ()
    assert clean_boot.operational.state()["quarantined"] == 1
    clear_event = clean_boot.operational.clear_quarantine(
        runtime_revision=clean_boot.runtime_revision, actor=owner,
        validation_evidence={"violations": []},
    )
    assert clear_event
    assert clean_boot.operational.state()["quarantined"] == 0
    kinds = [event["kind"] for event in clean_boot.operational.events(limit=10)]
    assert "quarantine_entered" in kinds
    assert "repair" in kinds
    assert "quarantine_cleared" in kinds


def test_quarantine_serves_health_and_diagnostics_but_refuses_normal_api(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from atlas_api.app import create_app

    root = tmp_path / "instance"
    rt = build_runtime(root)
    _owner, _cid, turn, _obligation_id = _commit_obligation(
        rt, "Corrupt this only for the quarantine test", kind="state_change"
    )
    with sqlite3.connect(root / "atlas-chat.db") as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute("UPDATE chat_turns SET intake_status=NULL WHERE turn_id=?", (turn["turn_id"],))
    monkeypatch.setenv("ATLAS_COMPANION_PASSWORD", "secret")
    monkeypatch.setenv("ATLAS_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("ATLAS_ENV", "development")

    with TestClient(create_app(instance_root=root, static_dir=tmp_path / "missing")) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["quarantined"] is True
        assert health.json()["operational_status"] == "quarantined"
        refused = client.get("/api/system")
        assert refused.status_code == 503
        assert refused.json()["code"] == "runtime_quarantined"
        login = client.post("/api/auth/login", json={"password": "secret"})
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        diag = client.get("/api/quarantine")
        assert diag.status_code == 200
        assert diag.json()["state"]["quarantined"] == 1
        assert any(
            row["code"] == "invalid_owner_intake"
            for row in diag.json()["current_violations"]
        )
        clear = client.post(
            "/api/quarantine/clear", headers={"X-CSRF-Token": csrf}, json={}
        )
        assert clear.status_code == 409
        assert clear.json()["code"] == "quarantine_validation_failed"
