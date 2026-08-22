from __future__ import annotations

from typing import Any

from .records import ExecutionRecord, WorkState


def control_state(metadata: dict[str, Any] | None) -> dict[str, Any]:
    raw = (metadata or {}).get("control")
    return dict(raw) if isinstance(raw, dict) else {}


def is_paused(state: WorkState) -> bool:
    return bool(control_state(state.metadata).get("paused"))


def is_pause_requested(state: WorkState) -> bool:
    return bool(control_state(state.metadata).get("pause_requested"))


def is_archived(state: WorkState) -> bool:
    return bool(control_state(state.metadata).get("archived"))


def running_executions(executions: tuple[ExecutionRecord, ...]) -> tuple[ExecutionRecord, ...]:
    return tuple(item for item in executions if item.status == "running")


def control_patch(**flags: Any) -> dict[str, Any]:
    return {"control": {key: value for key, value in flags.items()}}
