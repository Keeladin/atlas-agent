from __future__ import annotations

from typing import Any

from atlas_core.retrieval.capabilities import registry_fingerprint


_ACTIVE_WORK_STATUSES = {"staged", "queued", "runnable", "active"}
_BLOCKED_WORK_STATUSES = {"waiting", "paused", "failed"}
_SERVICED_OCCURRENCE_STATUSES = {"executing", "uncertain", "succeeded"}
_BLOCKED_OCCURRENCE_STATUSES = {"blocked", "failed"}


class AttentionRuntime:
    """Derived owner attention from obligation truth; never writes obligation state."""

    def __init__(self, obligations, work_store, *, registry=None) -> None:
        self.obligations = obligations
        self.work_store = work_store
        self.registry = registry

    def snapshot(self, *, limit: int = 500) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for obligation in self.obligations.list_open(limit=limit):
            turn = self.obligations.owner_turn_state(obligation.owner_turn_id)
            if turn.get("turn_completed_at") is None:
                # Chat still owns this turn. Intake preceding planning is not an owner-attention event.
                continue
            if turn.get("response_handed_off_at") is None:
                rows.append({
                    "kind": "handoff_unconfirmed",
                    "obligation_id": obligation.obligation_id,
                    "owner_turn_id": obligation.owner_turn_id,
                    "conversation_id": obligation.conversation_id,
                    "obligation_kind": obligation.kind,
                    "text": obligation.text,
                    "created_at": obligation.created_at,
                })
                continue

            servicing = self.work_store.servicing(obligation.obligation_id)
            active = []
            blocked = []
            for binding in servicing:
                occurrence_status = str(binding.get("occurrence_status") or "")
                if occurrence_status in _SERVICED_OCCURRENCE_STATUSES:
                    # A succeeded bound occurrence is still servicing truth until the ledger reconciles it.
                    active.append(binding)
                    continue
                if occurrence_status in _BLOCKED_OCCURRENCE_STATUSES:
                    blocked.append(binding)
                    continue
                work_status = str(binding.get("work_status") or "")
                if work_status in _ACTIVE_WORK_STATUSES:
                    active.append(binding)
                elif work_status in _BLOCKED_WORK_STATUSES:
                    blocked.append(binding)

            if not active and not blocked:
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
            elif blocked and not active:
                first = blocked[0]
                rows.append({
                    "kind": "servicing_blocked",
                    "obligation_id": obligation.obligation_id,
                    "owner_turn_id": obligation.owner_turn_id,
                    "conversation_id": obligation.conversation_id,
                    "obligation_kind": obligation.kind,
                    "text": obligation.text,
                    "work_id": first.get("work_id"),
                    "mechanism_kind": first.get("mechanism_kind"),
                    "mechanism_id": first.get("mechanism_id"),
                    "status": first.get("work_status") or first.get("occurrence_status"),
                    "created_at": obligation.created_at,
                })

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

        if self.registry is not None:
            current = registry_fingerprint(self.registry)
            for item in self.obligations.stale_unserviceable(current):
                rows.append({
                    "kind": "stale_unserviceable",
                    "obligation_id": item["obligation_id"],
                    "text": item["text"],
                    "assessment_id": item["assessment_id"],
                    "recorded_registry_fingerprint": item["registry_fingerprint"],
                    "current_registry_fingerprint": current,
                    "created_at": item["created_at"],
                })

        return tuple(rows[:max(1, int(limit))])
