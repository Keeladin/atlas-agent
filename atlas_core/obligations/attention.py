from __future__ import annotations

from typing import Any


_ACTIVE_SERVICING_STATUSES = {"staged", "queued", "active", "waiting", "paused"}


class AttentionRuntime:
    """Derived owner attention; never writes obligation truth."""

    def __init__(self, obligations, work_store) -> None:
        self.obligations = obligations
        self.work_store = work_store

    def snapshot(self, *, limit: int = 500) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for turn in self.obligations.intake_attention(limit=limit):
            rows.append({
                "kind": "incomplete_intake",
                "owner_turn_id": turn["turn_id"],
                "conversation_id": turn["conversation_id"],
                "intake_status": turn["intake_status"],
                "error_code": turn.get("intake_error_code"),
                "unmapped_spans": list(turn.get("unmapped_spans") or []),
                "created_at": turn["created_at"],
            })
        for obligation in self.obligations.list_open(limit=limit):
            servicing = self.work_store.servicing(obligation.obligation_id)
            active = [row for row in servicing if row["work_status"] in _ACTIVE_SERVICING_STATUSES]
            if not active:
                rows.append({
                    "kind": "unserviced_obligation",
                    "obligation_id": obligation.obligation_id,
                    "owner_turn_id": obligation.owner_turn_id,
                    "conversation_id": obligation.conversation_id,
                    "obligation_kind": obligation.kind,
                    "text": obligation.text,
                    "lapsed_at": obligation.lapsed_at,
                    "created_at": obligation.created_at,
                })
            for servicing_row in active:
                try:
                    work = self.work_store.get(servicing_row["work_id"])
                except KeyError:
                    continue
                gate = work.metadata.get("execution_gate") if isinstance(work.metadata.get("execution_gate"), dict) else {}
                if work.status == "waiting" and gate.get("reason") == "handoff_unconfirmed":
                    rows.append({
                        "kind": "handoff_unconfirmed",
                        "obligation_id": obligation.obligation_id,
                        "owner_turn_id": obligation.owner_turn_id,
                        "conversation_id": obligation.conversation_id,
                        "work_id": work.work_id,
                        "text": obligation.text,
                        "created_at": obligation.created_at,
                    })
                    break
            if obligation.lapsed_at is not None:
                rows.append({
                    "kind": "lapsed_obligation",
                    "obligation_id": obligation.obligation_id,
                    "owner_turn_id": obligation.owner_turn_id,
                    "conversation_id": obligation.conversation_id,
                    "obligation_kind": obligation.kind,
                    "text": obligation.text,
                    "lapsed_at": obligation.lapsed_at,
                    "created_at": obligation.created_at,
                })
        return tuple(rows[:max(1, int(limit))])
