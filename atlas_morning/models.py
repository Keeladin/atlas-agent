from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal


ClockKind = Literal["numeric", "sos", "eos", "open", "still_busy", "missing"]
ShiftKind = Literal["day", "night", "unknown"]


@dataclass(frozen=True)
class Message:
    sender: str
    timestamp: datetime
    text: str
    media_refs: tuple[str, ...] = ()

    @property
    def source_id(self) -> str:
        return f"{self.timestamp.isoformat(sep=' ')}|{self.sender}"


@dataclass
class ReportingUnit:
    message: Message
    extra_sources: list[Message] = field(default_factory=list)
    operational_day: date | None = None
    shift: ShiftKind = "unknown"
    late: bool = False
    association_uncertain: bool = False


@dataclass
class Entry:
    item: str
    item_key: str
    period_raw: str
    start: tuple[int, int] | None
    end: tuple[int, int] | None
    start_kind: ClockKind
    end_kind: ClockKind
    what_happened: str
    work_finding: str
    last_reported_state: str
    follow_up: str
    people: str
    work_character: str
    media_present: bool
    source_ref: str
    verbatim_exceptions: list[str] = field(default_factory=list)
    reported_work_interval: str = ""
    overlap_noted: bool = False
    flags: list[str] = field(default_factory=list)
    downtime_explicit: bool = False
    interval_ambiguous: bool = False


@dataclass
class Pack:
    operational_day: date
    units: list[ReportingUnit]
    entries: list[Entry]
    flags: list[str]
    loaded_messages: list[Message]
    relevant_messages: list[Message]
