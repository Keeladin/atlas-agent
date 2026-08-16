from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from atlas_morning.filter import combined_text
from atlas_morning.intervals import find_intervals
from atlas_morning.models import ReportingUnit, ShiftKind

NIGHT_LABEL = re.compile(
    r"\b(?:tmm\s+)?night\s+shift(?:\s+tmm)?\b|\bnight\s+shift\s+tmm\b|\btmm\s+night\b",
    re.I,
)
DAY_LABEL = re.compile(
    r"\b(?:tmm\s+)?day\s+shift\b|\b(?:daily|dialy)\s+report\b",
    re.I,
)
HEADING_DATE = re.compile(
    r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b"
)


def operational_day_containing(when: datetime, start_hour: int = 6) -> date:
    if when.hour < start_hour:
        return (when - timedelta(days=1)).date()
    return when.date()


def _heading_date(text: str) -> date | None:
    match = HEADING_DATE.search("\n".join(text.splitlines()[:6]))
    if not match:
        return None
    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _shift_from_clocks(text: str) -> ShiftKind | None:
    night = 0
    day = 0
    for interval in find_intervals(text):
        for kind, clock in (
            (interval.start_kind, interval.start),
            (interval.end_kind, interval.end),
        ):
            if kind != "numeric" or clock is None:
                continue
            hour = clock[0]
            if hour >= 18 or hour < 6:
                night += 1
            elif 6 <= hour <= 14:
                day += 1
    if night > day and night > 0:
        return "night"
    if day > night and day > 0:
        return "day"
    return None


def assign_unit(unit: ReportingUnit, config: dict[str, Any] | None = None) -> ReportingUnit:
    del config  # windows are context only; not used for SOS/EOS
    text = combined_text(unit)
    submitted = unit.message.timestamp
    heading = "\n".join(text.splitlines()[:6])

    described: ShiftKind | None = None
    if NIGHT_LABEL.search(heading):
        described = "night"
    elif DAY_LABEL.search(heading):
        described = "day"
    if described is None:
        described = _shift_from_clocks(text)

    uncertain = False
    heading_date = _heading_date(text)

    if described is None:
        if submitted.hour < 6:
            described = "night"
        elif submitted.hour >= 14:
            described = "day"
        else:
            described = "unknown"
            uncertain = True

    if heading_date is not None and described != "unknown":
        op_day = heading_date
        # A heading date on a night report is the evening/operating date.
        # If it looks like a morning calendar date after midnight, still trust heading.
    elif described == "night":
        # Before 06:00: night that belongs to the operational day that started yesterday.
        # After 06:00: late post of that same night — still yesterday's operational day.
        if submitted.hour < 6:
            op_day = (submitted - timedelta(days=1)).date()
        else:
            op_day = (submitted - timedelta(days=1)).date()
            unit.late = True
    elif described == "day":
        if submitted.hour < 6:
            op_day = (submitted - timedelta(days=1)).date()
        else:
            op_day = submitted.date()
    else:
        op_day = operational_day_containing(submitted)
        uncertain = True

    if described == "night" and submitted.hour == 5 and submitted.minute >= 30:
        unit.late = True
    if described == "night" and submitted.hour >= 6:
        unit.late = True

    unit.operational_day = op_day
    unit.shift = described
    unit.association_uncertain = uncertain
    return unit


def assign_units(units: list[ReportingUnit], config: dict[str, Any]) -> list[ReportingUnit]:
    return [assign_unit(unit, config) for unit in units]


def select_pack(
    units: list[ReportingUnit],
    operational_day: date,
) -> list[ReportingUnit]:
    return [unit for unit in units if unit.operational_day == operational_day]


def missing_shift_flags(units: list[ReportingUnit]) -> list[str]:
    flags: list[str] = []
    if not any(unit.shift == "day" for unit in units):
        flags.append("Expected day-shift report missing from the pack")
    if not any(unit.shift == "night" for unit in units):
        flags.append("Expected night-shift report missing from the pack")
    return flags
