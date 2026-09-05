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


def collect_runtime_violations(chat_store, obligation_store, work_store, actions=None, evidence=None):
    """Executable mirror of every forbidden persisted shape in specification section 15."""
    violations: list[RuntimeInvariantViolation] = []

    for turn_id in chat_store.invalid_owner_intakes():
        _append(violations, "invalid_owner_intake", turn_id, "owner turn intake state is invalid")

    for work_id in work_store.staged_without_bindings():
        _append(violations, "staged_work_unbacked", work_id, "staged Work has no obligation binding")

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
            valid = ref.startswith("evidence:") or ref.startswith("action:")
            if not valid:
                _append(violations, "state_change_resolution_missing_evidence", row["obligation_id"], ref)
                continue
            if ref.startswith("evidence:") and evidence is not None:
                try:
                    record = evidence.get(ref.split(":", 1)[1])
                    occurrence = actions.get(record.occurrence_id) if actions is not None else None
                    if occurrence is not None and occurrence.status != "succeeded":
                        raise ValueError("evidence occurrence is not succeeded")
                except (KeyError, ValueError):
                    _append(violations, "state_change_resolution_missing_evidence", row["obligation_id"], ref)
            if ref.startswith("action:") and actions is not None:
                try:
                    occurrence = actions.get(ref.split(":", 1)[1])
                    if not (occurrence.status == "blocked" and occurrence.policy_decision == "NO"):
                        raise ValueError("action is not authoritative refusal")
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

        reports = db.execute("""SELECT turn_id,metadata_json FROM chat_turns
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


def mapped_forbidden_state_checks() -> tuple[str, ...]:
    """CI contract: every frozen Section 15 bullet has a named executable check."""
    return FORBIDDEN_STATE_CHECK_IDS
