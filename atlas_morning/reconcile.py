from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atlas_morning.intervals import Interval, intervals_overlap
from atlas_morning.models import Entry

CONTINUITY_EVIDENCE = (
    "cont to",
    "continue",
    "still busy",
    "ask ",
    "ns to",
    "ms to",
    "night shift to",
    "day shift to",
    "not complete",
    "still under repair",
    "awaiting spare",
    "awaiting part",
    "await spare",
    "to be continued",
    "handover",
)


def _as_interval(entry: Entry) -> Interval:
    return Interval(
        raw=entry.period_raw,
        start=entry.start,
        end=entry.end,
        start_kind=entry.start_kind,
        end_kind=entry.end_kind,
    )


def _explicit_single_continue(entry: Entry) -> bool:
    blob = f"{entry.what_happened} {entry.work_finding}".lower()
    return blob.startswith("cont to") or "cont to assemble" in blob


def flag_entries(entries: list[Entry]) -> list[Entry]:
    by_item: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        by_item.setdefault(entry.item_key, []).append(index)

    for key, indexes in by_item.items():
        if len(indexes) < 2:
            continue
        for i, left_i in enumerate(indexes):
            for right_i in indexes[i + 1 :]:
                left = entries[left_i]
                right = entries[right_i]
                if _explicit_single_continue(left) and left_i == right_i:
                    continue
                overlap = intervals_overlap(_as_interval(left), _as_interval(right))
                if overlap:
                    left.overlap_noted = True
                    right.overlap_noted = True
                    _add_flag(
                        left,
                        f"Overlapping reported work intervals on {left.item} — not summed, not downtime",
                    )
                    _add_flag(
                        right,
                        f"Overlapping reported work intervals on {right.item} — not summed, not downtime",
                    )
                blob = " ".join(
                    [
                        left.what_happened,
                        left.work_finding,
                        left.follow_up,
                        right.what_happened,
                        right.work_finding,
                        right.follow_up,
                    ]
                ).lower()
                if any(hint in blob for hint in CONTINUITY_EVIDENCE):
                    _add_flag(
                        left,
                        f"Possible continuation of earlier {left.item} work — confirm",
                    )
                    _add_flag(
                        right,
                        f"Possible continuation of earlier {right.item} work — confirm",
                    )

        states = {
            entries[i].last_reported_state
            for i in indexes
            if entries[i].last_reported_state != "State not established from report"
        }
        if len(states) > 1:
            for i in indexes:
                _add_flag(
                    entries[i],
                    f"Conflicting last-reported states for {entries[i].item} — both kept",
                )

    for entry in entries:
        if entry.work_character == "attendance":
            continue
        if entry.interval_ambiguous:
            _add_flag(
                entry,
                f"Ambiguous night-shift times ({entry.period_raw}) — "
                f"duration withheld; confirm start/end",
            )
        if entry.start_kind != "numeric" or entry.end_kind != "numeric":
            if entry.period_raw and entry.period_raw != "not stated":
                _add_flag(entry, f"Reported period incomplete ({entry.period_raw})")
        # Last-reported state may be "State not established from report" on the
        # row. That is not a high-priority exception by itself.
        if entry.last_reported_state in {
            "Not tested",
            "Awaiting spares",
            "Still under repair",
        }:
            _add_flag(entry, entry.last_reported_state)
        if entry.media_present:
            _add_flag(entry, "Media referenced or attached — not interpreted")
        if not entry.item_key:
            _add_flag(entry, "Item identity uncertain")
    return entries


def _add_flag(entry: Entry, flag: str) -> None:
    if flag not in entry.flags:
        entry.flags.append(flag)


def load_corrections(path: str | Path | None) -> dict[str, Any]:
    if path is None or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_corrections(path: str | Path, data: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def apply_corrections(entries: list[Entry], corrections: dict[str, Any]) -> list[Entry]:
    dismiss = {
        str(item).upper()
        for item in (corrections.get("dismiss_continuity_items") or [])
    }
    state_overrides = corrections.get("state_overrides") or {}
    for entry in entries:
        if entry.item_key.upper() in dismiss or entry.item.upper() in dismiss:
            entry.flags = [
                flag
                for flag in entry.flags
                if "Possible continuation" not in flag
            ]
        key = entry.item_key
        if key in state_overrides:
            entry.last_reported_state = str(state_overrides[key])
        if entry.item in state_overrides:
            entry.last_reported_state = str(state_overrides[entry.item])
    return entries


def ordinary_unflagged(entry: Entry) -> bool:
    if entry.flags:
        return False
    if entry.start_kind != "numeric" or entry.end_kind != "numeric":
        return False
    if entry.last_reported_state == "State not established from report":
        return False
    return True
