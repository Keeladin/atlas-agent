from __future__ import annotations

import re
from dataclasses import dataclass

from atlas_morning.models import ClockKind

TOKEN = r"(?:sos|eos|eoe|still\s+busy|\d{1,2}\s*[h:.]\s*\d{2}|\d{1,2}\s*h\b)"
INTERVAL_RE = re.compile(
    rf"(?P<a>{TOKEN})\s*(?:[-–—=]{{1,2}}|to|tot)\s*(?P<b>{TOKEN})",
    re.I,
)


@dataclass(frozen=True)
class Interval:
    raw: str
    start: tuple[int, int] | None
    end: tuple[int, int] | None
    start_kind: ClockKind
    end_kind: ClockKind


def parse_clock_token(token: str) -> tuple[tuple[int, int] | None, ClockKind]:
    raw = re.sub(r"\s+", "", token).lower()
    if raw == "sos":
        return None, "sos"
    if raw in {"eos", "eoe"}:
        return None, "eos"
    if raw.startswith("stillbusy"):
        return None, "still_busy"
    match = re.fullmatch(r"(\d{1,2})[h:.](\d{2})", raw)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour == 24:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour, minute), "numeric"
        return None, "missing"
    match = re.fullmatch(r"(\d{1,2})h", raw)
    if match:
        hour = int(match.group(1))
        if 0 <= hour <= 23:
            return (hour, 0), "numeric"
        return None, "missing"
    return None, "missing"


def find_intervals(text: str) -> list[Interval]:
    found: list[Interval] = []
    for match in INTERVAL_RE.finditer(text):
        start, start_kind = parse_clock_token(match.group("a"))
        end, end_kind = parse_clock_token(match.group("b"))
        found.append(
            Interval(
                raw=match.group(0).strip(),
                start=start,
                end=end,
                start_kind=start_kind,
                end_kind=end_kind,
            )
        )
    return found


def minutes_between(start: tuple[int, int], end: tuple[int, int]) -> int:
    start_m = start[0] * 60 + start[1]
    end_m = end[0] * 60 + end[1]
    if end_m < start_m:
        end_m += 24 * 60
    return end_m - start_m


def format_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours} h {mins:02d} min"


def wraps_midnight(interval: Interval) -> bool:
    if interval.start_kind != "numeric" or interval.end_kind != "numeric":
        return False
    if interval.start is None or interval.end is None:
        return False
    start_m = interval.start[0] * 60 + interval.start[1]
    end_m = interval.end[0] * 60 + interval.end[1]
    return end_m < start_m


def suspicious_night_wrap(interval: Interval, shift: str | None) -> bool:
    """Night report, start written as a daytime clock, end after midnight.

    Do not rewrite 10:30 → 22:30. Withhold the calculated duration.
    """
    if shift != "night":
        return False
    if not wraps_midnight(interval):
        return False
    if interval.start is None:
        return False
    hour = interval.start[0]
    return 6 <= hour <= 17


def reported_work_interval(interval: Interval, shift: str | None = None) -> str:
    if suspicious_night_wrap(interval, shift):
        return ""
    if interval.start_kind == "numeric" and interval.end_kind == "numeric":
        if interval.start is None or interval.end is None:
            return ""
        return format_duration(minutes_between(interval.start, interval.end))
    return ""


def intervals_overlap(a: Interval, b: Interval) -> bool:
    if not (
        a.start_kind == "numeric"
        and a.end_kind == "numeric"
        and b.start_kind == "numeric"
        and b.end_kind == "numeric"
    ):
        return False
    if None in (a.start, a.end, b.start, b.end):
        return False
    a0 = a.start[0] * 60 + a.start[1]
    a1 = a.end[0] * 60 + a.end[1]
    b0 = b.start[0] * 60 + b.start[1]
    b1 = b.end[0] * 60 + b.end[1]
    if a1 < a0:
        a1 += 24 * 60
    if b1 < b0:
        b1 += 24 * 60
    return a0 < b1 and b0 < a1
