from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


FORBIDDEN_STATE_CHECK_IDS = (
    "invalid_owner_intake",
    "staged_work_unbacked",
    "runnable_work_intake_not_complete",
    "runnable_work_without_handoff",
    "occurrence_before_complete_intake",
    "invalid_obligation_grounding",
    "obligation_schema_contains_execution_fields",
    "open_obligation_has_resolution",
    "resolved_obligation_missing_resolution",
    "communication_resolution_missing_chat",
    "state_change_resolution_missing_evidence",
    "withdrawal_missing_owner_authority",
    "withdrawal_nonowner_authority",
    "lapse_on_nonopen_obligation",
    "invalid_supersession_order",
    "unserviceable_reopened_or_unproven",
    "persisted_reportable_state",
    "communication_resolved_before_chat",
    "stale_or_unproven_report_snapshot",
    "report_outstanding_over_fulfilled_evidence",
    "handoff_without_transport_provenance",
)


@dataclass(frozen=True)
class RuntimeInvariantViolation:
    code: str
    reference: str
    detail: str


class RuntimeInvariantError(RuntimeError):
    def __init__(self, violations) -> None:
        self.violations = tuple(violations)
        summary = "; ".join(f"{x.code}:{x.reference}" for x in self.violations[:12])
        super().__init__(f"Atlas runtime invariants failed: {summary}")
def _append(rows: list[RuntimeInvariantViolation], code: str, reference: Any, detail: str) -> None:
    rows.append(RuntimeInvariantViolation(code, str(reference), detail))


def _chat_rows(chat_store, sql: str, args: tuple[Any, ...] = ()):
    with chat_store._db() as db:
        return db.execute(sql, args).fetchall()


def _work_rows(work_store, sql: str, args: tuple[Any, ...] = ()):
    with work_store._db() as db:
        return db.execute(sql, args).fetchall()


def _authoritative_evidence_before(work_store, actions, evidence, obligation_id: str, cutoff: str) -> str | None:
    """Evidence that already satisfied an obligation's fulfilment basis at `cutoff`, if any."""
    for binding in work_store.servicing(obligation_id):
        mechanism_kind = str(binding.get("mechanism_kind") or "")
        mechanism_id = str(binding.get("mechanism_id") or "")
        occurrence_id = mechanism_id if mechanism_kind == "occurrence" else ""
        if mechanism_kind == "work_step":
            try:
                occurrence_id = str(work_store.step(mechanism_id).occurrence_id or "")
            except KeyError:
                continue
        if not occurrence_id:
            continue
        try:
            occurrence = actions.get(occurrence_id)
        except KeyError:
            continue
        if occurrence.status != "succeeded":
            continue
        for record in evidence.for_occurrence(occurrence_id):
            payload = record.payload if isinstance(record.payload, dict) else {}
            if record.kind == "execution_receipt" and payload.get("ok") is True and str(record.created_at) <= cutoff:
                return record.evidence_id
    return None


def collect_runtime_violations(chat_store, obligation_store, work_store, actions=None, evidence=None):
    """Executable mirror of every forbidden persisted shape in specification section 15."""
    violations: list[RuntimeInvariantViolation] = []

    for turn_id in chat_store.invalid_owner_intakes():
        _append(violations, "invalid_owner_intake", turn_id, "owner turn intake state is invalid")

    for work in work_store.list(limit=10000):
        if work.status != "staged":
            continue
        valid_backing = False
        origin = work.metadata.get("chat_origin") if isinstance(work.metadata.get("chat_origin"), dict) else {}
        owner_turn_id = str(origin.get("owner_turn_id") or "")
        for binding in work_store.bindings(work.work_id):
            if binding.get("mechanism_kind") != "work_step":
                continue
            try:
                step = work_store.step(str(binding.get("mechanism_id") or ""))
                obligation = obligation_store.get(str(binding.get("obligation_id") or ""))
            except KeyError:
                continue
            if step.work_id != work.work_id or obligation.status != "open":
                continue
            if obligation.owner_principal_id != work.owner_principal_id:
                continue
            if owner_turn_id and obligation.owner_turn_id != owner_turn_id:
                continue
            valid_backing = True
            break
        if not valid_backing:
            _append(violations, "staged_work_unbacked", work.work_id, "staged Work has no valid open step-level obligation binding")

    for work in work_store.list(limit=10000):
        origin = work.metadata.get("chat_origin") if isinstance(work.metadata.get("chat_origin"), dict) else None
        if not origin or work.status != "runnable":
            continue
        owner_turn_id = str(origin.get("owner_turn_id") or "")
        try:
            turn = chat_store.turn(owner_turn_id)
        except KeyError:
            _append(violations, "runnable_work_intake_not_complete", work.work_id, "owner turn is missing")
            continue
        if turn.get("intake_status") != "complete":
            _append(violations, "runnable_work_intake_not_complete", work.work_id, owner_turn_id)
        if not turn.get("turn_completed_at") or not turn.get("response_handed_off_at"):
            _append(violations, "runnable_work_without_handoff", work.work_id, owner_turn_id)
    if actions is not None:
        for occurrence in actions.recent(limit=10000):
            if not occurrence.work_id:
                continue
            try:
                work = work_store.get(occurrence.work_id)
            except KeyError:
                continue
            origin = work.metadata.get("chat_origin") if isinstance(work.metadata.get("chat_origin"), dict) else None
            if not origin:
                continue
            owner_turn_id = str(origin.get("owner_turn_id") or "")
            try:
                turn = chat_store.turn(owner_turn_id)
            except KeyError:
                _append(violations, "occurrence_before_complete_intake", occurrence.occurrence_id, "owner turn missing")
                continue
            if turn.get("intake_status") != "complete":
                _append(violations, "occurrence_before_complete_intake", occurrence.occurrence_id, owner_turn_id)

        with work_store._db() as work_db:
            direct_bindings = work_db.execute(
                """SELECT obligation_id,mechanism_id FROM obligation_bindings
                   WHERE mechanism_kind='occurrence'"""
            ).fetchall()
        for binding in direct_bindings:
            try:
                obligation = obligation_store.get(binding["obligation_id"])
                turn = chat_store.turn(obligation.owner_turn_id)
            except KeyError:
                _append(violations, "occurrence_before_complete_intake", binding["mechanism_id"], "direct binding authority is missing")
                continue
            if turn.get("intake_status") != "complete":
                _append(violations, "occurrence_before_complete_intake", binding["mechanism_id"], obligation.owner_turn_id)

    for obligation_id in obligation_store.grounding_violations():
        _append(violations, "invalid_obligation_grounding", obligation_id, "grounding does not match owner turn")

    with obligation_store._db() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(obligations)")}
        forbidden_columns = {"capability_id", "work_id", "step_id", "dependency_id", "ordering_edge"}
        if columns & forbidden_columns:
            _append(violations, "obligation_schema_contains_execution_fields", "obligations", ",".join(sorted(columns & forbidden_columns)))
        rows = db.execute("""SELECT obligation_id FROM obligations WHERE status='open'
                             AND (resolution_kind IS NOT NULL OR resolution_ref IS NOT NULL OR resolved_at IS NOT NULL)""").fetchall()
        for row in rows:
            _append(violations, "open_obligation_has_resolution", row["obligation_id"], "open obligation carries resolution data")
        rows = db.execute("""SELECT obligation_id FROM obligations WHERE status='resolved'
                             AND (resolution_kind IS NULL OR resolution_ref IS NULL OR resolved_at IS NULL)""").fetchall()
        for row in rows:
            _append(violations, "resolved_obligation_missing_resolution", row["obligation_id"], "resolved obligation lacks authority")
        comm = db.execute("""SELECT obligation_id,resolution_ref,resolved_at FROM obligations
                            WHERE kind='communication' AND status='resolved' AND resolution_kind='fulfilled'""").fetchall()
        for row in comm:
            ref = str(row["resolution_ref"] or "")
            if not ref.startswith("chat_turn:"):
                _append(violations, "communication_resolution_missing_chat", row["obligation_id"], ref)
                continue
            turn_id = ref.split(":", 1)[1]
            turn = db.execute("SELECT role,created_at FROM chat_turns WHERE turn_id=?", (turn_id,)).fetchone()
            if turn is None or turn["role"] != "assistant":
                _append(violations, "communication_resolution_missing_chat", row["obligation_id"], turn_id)
            elif row["resolved_at"] and str(turn["created_at"]) > str(row["resolved_at"]):
                _append(violations, "communication_resolved_before_chat", row["obligation_id"], turn_id)

        state_rows = db.execute("""SELECT obligation_id,resolution_ref FROM obligations
                                  WHERE kind='state_change' AND status='resolved' AND resolution_kind='fulfilled'""").fetchall()
        for row in state_rows:
            ref = str(row["resolution_ref"] or "")
            if not ref.startswith("evidence:"):
                _append(violations, "state_change_resolution_missing_evidence", row["obligation_id"], ref)
                continue
            if evidence is not None:
                try:
                    record = evidence.get(ref.split(":", 1)[1])
                    occurrence = actions.get(record.occurrence_id) if actions is not None else None
                    if occurrence is not None and occurrence.status != "succeeded":
                        raise ValueError("evidence occurrence is not succeeded")
                    payload = record.payload if isinstance(record.payload, dict) else {}
                    if (
                        record.kind != "obligation_fulfilment_verification"
                        or payload.get("fulfilled") is not True
                        or payload.get("obligation_id") != row["obligation_id"]
                        or not str(payload.get("evidence_digest") or "")
                    ):
                        raise ValueError("evidence is not a grounded obligation fulfilment verification")
                except (KeyError, ValueError):
                    _append(violations, "state_change_resolution_missing_evidence", row["obligation_id"], ref)
        withdrawn = db.execute("""SELECT obligation_id,owner_principal_id,owner_turn_id,resolution_ref
                                 FROM obligations WHERE status='withdrawn'""").fetchall()
        for row in withdrawn:
            events = db.execute("""SELECT payload_json FROM obligation_events
                                  WHERE obligation_id=? AND kind='withdrawn_by_owner'
                                  ORDER BY created_at DESC,rowid DESC LIMIT 1""", (row["obligation_id"],)).fetchone()
            if events is None:
                _append(violations, "withdrawal_missing_owner_authority", row["obligation_id"], "owner event missing")
                continue
            import json
            payload = json.loads(events["payload_json"] or "{}")
            actor_turn_id = str(payload.get("actor_turn_id") or "")
            actor = db.execute("SELECT rowid,* FROM chat_turns WHERE turn_id=?", (actor_turn_id,)).fetchone()
            source = db.execute("SELECT rowid FROM chat_turns WHERE turn_id=?", (row["owner_turn_id"],)).fetchone()
            grounded = str(payload.get("grounding_excerpt") or "")
            if actor is None or actor["role"] != "user" or actor["owner_principal_id"] != row["owner_principal_id"]:
                _append(violations, "withdrawal_nonowner_authority", row["obligation_id"], actor_turn_id)
            elif not grounded or grounded not in actor["content"] or source is None or int(actor["rowid"]) <= int(source["rowid"]):
                _append(violations, "withdrawal_missing_owner_authority", row["obligation_id"], actor_turn_id)

        for row in db.execute("SELECT obligation_id FROM obligations WHERE lapsed_at IS NOT NULL AND status!='open'").fetchall():
            _append(violations, "lapse_on_nonopen_obligation", row["obligation_id"], "lapse annotation survived terminal state")

        superseded = db.execute("""SELECT old.obligation_id AS old_id,new.obligation_id AS new_id,
                                   old.owner_turn_id AS old_turn,new.owner_turn_id AS new_turn
                                   FROM obligations new JOIN obligations old ON old.obligation_id=new.supersedes""").fetchall()
        for row in superseded:
            old_turn = db.execute("SELECT rowid FROM chat_turns WHERE turn_id=?", (row["old_turn"],)).fetchone()
            new_turn = db.execute("SELECT rowid FROM chat_turns WHERE turn_id=?", (row["new_turn"],)).fetchone()
            if old_turn is None or new_turn is None or int(new_turn["rowid"]) <= int(old_turn["rowid"]):
                _append(violations, "invalid_supersession_order", row["new_id"], row["old_id"])
        stale_unserviceable = db.execute("""SELECT a.assessment_id,a.obligation_id,o.status,o.resolution_kind
                                          FROM serviceability_assessments a
                                          JOIN obligations o ON o.obligation_id=a.obligation_id
                                          WHERE o.status!='resolved' OR o.resolution_kind!='unserviceable'""").fetchall()
        for row in stale_unserviceable:
            _append(violations, "unserviceable_reopened_or_unproven", row["obligation_id"], row["assessment_id"])

        reportable_columns = []
        for table in ("obligations", "chat_turns", "obligation_events"):
            for column in db.execute(f"PRAGMA table_info({table})").fetchall():
                if str(column[1]).casefold() == "reportable":
                    reportable_columns.append(f"{table}.reportable")
        if reportable_columns:
            _append(violations, "persisted_reportable_state", "schema", ",".join(reportable_columns))

        reports = db.execute("""SELECT turn_id,created_at,metadata_json FROM chat_turns
                              WHERE role='assistant' AND json_extract(metadata_json,'$.obligation_report') IS NOT NULL""").fetchall()
        for row in reports:
            import json
            meta = json.loads(row["metadata_json"] or "{}")
            report = meta.get("obligation_report") if isinstance(meta, dict) else None
            ids = report.get("obligation_ids") if isinstance(report, dict) else None
            revisions = report.get("obligation_revisions") if isinstance(report, dict) else None
            evidence_ids = report.get("evidence_ids") if isinstance(report, dict) else None
            if not isinstance(ids, list) or not isinstance(revisions, dict) or not isinstance(evidence_ids, list):
                _append(violations, "stale_or_unproven_report_snapshot", row["turn_id"], "snapshot metadata incomplete")
                continue
            bad = False
            for obligation_id in ids:
                obligation = db.execute("SELECT revision,resolution_ref FROM obligations WHERE obligation_id=?", (obligation_id,)).fetchone()
                base = revisions.get(obligation_id)
                if obligation is None or not isinstance(base, int) or int(obligation["revision"]) != base + 1 or obligation["resolution_ref"] != f"chat_turn:{row['turn_id']}":
                    bad = True
                    break
            if not bad and evidence is not None:
                for evidence_id in evidence_ids:
                    try:
                        evidence.get(str(evidence_id))
                    except KeyError:
                        bad = True
                        break
            if bad:
                _append(violations, "stale_or_unproven_report_snapshot", row["turn_id"], "snapshot authority is stale or missing")

            outstanding = report.get("outstanding_obligation_revisions") if isinstance(report, dict) else None
            states = report.get("outstanding_obligation_states") if isinstance(report, dict) else None
            if outstanding:
                if not isinstance(states, dict) or set(states) != set(outstanding):
                    _append(violations, "report_outstanding_over_fulfilled_evidence", row["turn_id"],
                            "outstanding servicing state is missing from the report basis")
                elif actions is not None and evidence is not None:
                    for obligation_id, state in states.items():
                        if state != "unserviced":
                            continue
                        proof = _authoritative_evidence_before(
                            work_store, actions, evidence, str(obligation_id), str(row["created_at"])
                        )
                        if proof is not None:
                            _append(violations, "report_outstanding_over_fulfilled_evidence", row["turn_id"],
                                    f"{obligation_id} was already evidenced by {proof}")
    handed = _chat_rows(chat_store, """SELECT t.turn_id FROM chat_turns t
                                      WHERE t.role='user' AND t.response_handed_off_at IS NOT NULL
                                        AND NOT EXISTS(
                                            SELECT 1 FROM response_handoff_events h
                                            WHERE h.owner_turn_id=t.turn_id AND h.source='asgi_final_body'
                                        )""")
    for row in handed:
        _append(violations, "handoff_without_transport_provenance", row["turn_id"], "ASGI final-body event missing")

    return tuple(violations)


def assert_runtime_invariants(chat_store, obligation_store, work_store, actions=None, evidence=None) -> None:
    violations = collect_runtime_violations(chat_store, obligation_store, work_store, actions, evidence)
    if violations:
        raise RuntimeInvariantError(violations)
