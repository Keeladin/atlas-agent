from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_FENCED_JSON_RE = re.compile(r"```(?:json|JSON)?\s*\r?\n?(.*?)```", re.DOTALL)
_FORBIDDEN_BRIEF_KEYS = frozenset(
    {
        "task_id",
        "work_id",
        "step_id",
        "execution_id",
        "tool_name",
        "provider_name",
        "mcp_name",
        "n8n_name",
        "authority_scope",
    }
)


@dataclass(frozen=True)
class InterpretedIntent:
    objective: str
    notes: str | None = None


def interpret(objective: str, *, notes: str | None = None) -> InterpretedIntent:
    text = (objective or "").strip()
    if not text:
        raise ValueError("objective is required")
    extra = (notes or "").strip() or None
    return InterpretedIntent(objective=text, notes=extra)


def parse_brief_payload(text: str) -> dict[str, Any]:
    payload = _parse_json_object(text)
    forbidden = sorted(key for key in payload if key in _FORBIDDEN_BRIEF_KEYS)
    if forbidden:
        raise ValueError(f"TaskBrief payload contains forbidden keys: {forbidden}")
    capabilities = _as_capability_ids(payload.get("capabilities"))
    constraints = _as_strings(payload.get("constraints"))
    expected = payload.get("expected_effect")
    kind = payload.get("deliverable_kind")
    notes = payload.get("notes")
    reason = payload.get("reason")
    closest = payload.get("closest_capability")
    return {
        "objective": str(payload.get("objective") or "").strip(),
        "capabilities": capabilities,
        "expected_effect": str(expected).strip() if expected is not None else "",
        "constraints": constraints,
        "deliverable_kind": str(kind).strip() if kind else None,
        "notes": str(notes).strip() if notes else None,
        "reason": str(reason).strip() if reason else None,
        "closest_capability": str(closest).strip() if closest else None,
    }


def _as_capability_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise ValueError("capabilities must be a list of capability ids")
    return tuple(str(item).strip() for item in items if str(item).strip())


def _as_strings(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise ValueError("constraints must be a list of strings")
    return tuple(str(item).strip() for item in items if str(item).strip())


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip().lstrip("\ufeff")
    if not raw:
        raise ValueError("Advanced did not return a Task Brief.")
    candidates = [raw]
    for block in _FENCED_JSON_RE.findall(raw):
        inner = block.strip()
        if inner:
            candidates.append(inner)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    start = raw.find("{")
    if start >= 0:
        try:
            value, _end = json.JSONDecoder().raw_decode(raw, start)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
    raise ValueError("Advanced did not return a Task Brief JSON object.")
