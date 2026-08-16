from __future__ import annotations

import re
from typing import Any

from atlas_morning.config import item_key
from atlas_morning.filter import (
    ATTENDANCE_RE,
    HEADING_RE,
    ITEM_RE,
    combined_text,
    is_plausible_equipment_id,
)
from atlas_morning.intervals import (
    Interval,
    find_intervals,
    reported_work_interval,
    suspicious_numeric_interval,
)
from atlas_morning.models import Entry, ReportingUnit

PEOPLE_LINE = re.compile(
    r"^[A-Za-z][A-Za-z .'-]{1,40}(?:\s*[&,]\s*[A-Za-z][A-Za-z .'-]{1,40}){0,6}$"
)
SKIP_LINE = re.compile(r"^(👍🏼|💥)\s*$")
HANDOVER = re.compile(
    r"\b(?:ns|ms|night\s*shift|day\s*shift)\s+to\b|"
    r"\bask\s+\w+\s+to\b|"
    r"\bcont(?:inue)?\s+to\b|"
    r"\bneeds?\b",
    re.I,
)
EXPLICIT_CONTINUE = re.compile(r"\bcont(?:inue)?\s+to\b", re.I)
NOT_TESTED = re.compile(r"\bno operator\b|\bnot tested\b", re.I)
AWAIT_PARTS = re.compile(r"\bawait(?:ing)?\s+(?:spare|spares|part|parts)\b", re.I)
STILL_REPAIR = re.compile(
    r"\bstill under repair\b|\bnot complete\b|\bstill busy\b",
    re.I,
)
RUNNING = re.compile(
    r"\brunning\b|\ball in order\b|\ball ok\b|\ball good\b|\breported complete\b",
    re.I,
)
COMPLETE = re.compile(r"\bcomplete\b|\btested machine all good\b", re.I)
DOWNTIME = re.compile(r"\bdowntime\b|\bunavailable\b", re.I)
PHOTOS = re.compile(r"\bphotos?\b", re.I)
TERMINAL_STATUS = re.compile(
    r"^(running|all in order|all ok|all good|reported complete)\.?\s*$",
    re.I,
)
WORK_VERB = re.compile(
    r"^(remove|repair|replace|fit|weld|inspect|collect|assist|jumpstart|"
    r"jump\s+start|service|strip|install|recover|transport)\b",
    re.I,
)
MACHINE_TOKEN = re.compile(
    r"\b([A-Za-z]{2,10}(?:[\s\-]gt)?[\s\-]*\d{1,4}[A-Za-z]?|L\s*\d{2,4}[A-Za-z]?)\b",
    re.I,
)
ALL_AT_WORK = re.compile(r"^all at work\b", re.I)
LABOR_ONLY = re.compile(r"^labou?r\.?\s*100%?\s*\.?$", re.I)
REPORT_LEVEL_RE = re.compile(
    r"^(?:"
    r"long\s+standing\b|"
    r"stop\s+and\s+fix\b|"
    r"empty\s+(?:scotch|parts)\s+car\b|"
    r"load\s+\d+\s*x\b|"
    r"into\s+parts\s+car\b|"
    r"take\s+.+\s+to\s+station\b|"
    r"standing\s+\d"
    r")",
    re.I,
)
FARM_GATES = re.compile(r"\bfarm\s+gates?\b", re.I)
GLUED_HEADER = re.compile(
    r"^(L\d{2,3}|[A-Za-z]{2,10}\d{1,3})(\d{1,2}\s*[h:]\s*\d{2}\b.*)$",
    re.I,
)


def _unglue_header(line: str) -> str:
    stripped = line.strip()
    for pattern in (
        r"^(L\d{2})(\d{1,2}\s*[h:]\s*\d{2}\b.*)$",
        r"^(L\d{3})(\d{1,2}\s*[h:]\s*\d{2}\b.*)$",
        r"^([A-Za-z]{2,10}\d{1,3})(\d{1,2}\s*[h:]\s*\d{2}\b.*)$",
    ):
        match = re.match(pattern, stripped, re.I)
        if match and is_plausible_equipment_id(match.group(1)):
            return f"{match.group(1)} {match.group(2)}"
    return line


def _is_item_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or HEADING_RE.match(stripped):
        return False
    if ATTENDANCE_RE.match(stripped):
        return False
    match = ITEM_RE.match(stripped)
    if not match:
        return False
    return is_plausible_equipment_id(match.group(1))


def _looks_like_new_activity(line: str) -> bool:
    stripped = line.strip()
    if not stripped or ATTENDANCE_RE.match(stripped) or HEADING_RE.match(stripped):
        return False
    if PEOPLE_LINE.match(stripped) and not MACHINE_TOKEN.search(stripped):
        return False
    if find_intervals(stripped) and len(stripped) < 24:
        return False
    if any(
        is_plausible_equipment_id(m.group(1))
        for m in MACHINE_TOKEN.finditer(stripped)
    ) and not _is_item_line(stripped):
        return True
    return bool(WORK_VERB.match(stripped))


def _item_from_free_text(line: str) -> str:
    mentioned = [
        m.group(1).strip()
        for m in MACHINE_TOKEN.finditer(line)
        if is_plausible_equipment_id(m.group(1))
    ]
    # Prefer L-numbers and other ids in order written
    unique: list[str] = []
    seen: set[str] = set()
    for token in mentioned:
        key = re.sub(r"\s+", "", token).upper()
        if key not in seen:
            seen.add(key)
            unique.append(token.strip())
    if len(unique) >= 2:
        return f"{unique[0]} → {unique[1]}"
    if len(unique) == 1:
        return unique[0]
    return "Unassigned work"


def _item_from_line(line: str) -> str:
    stripped = line.strip()
    if ATTENDANCE_RE.match(stripped):
        return stripped.split("\n")[0][:80]
    match = ITEM_RE.match(stripped)
    if not match:
        return stripped[:80]
    # Keep the identifier only; remainder of the line is body/time.
    return match.group(1).strip()


def _split_item_and_rest(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if ATTENDANCE_RE.match(stripped):
        return stripped, ""
    match = ITEM_RE.match(stripped)
    if not match:
        return stripped[:80], ""
    item = match.group(1).strip()
    rest = stripped[match.end() :].strip(" -–—:\t")
    return item, rest


def split_blocks(text: str) -> list[list[str]]:
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append(current)
            current = []

    for raw in lines:
        stripped = _unglue_header(raw.strip()).strip()
        if not stripped:
            continue
        if HEADING_RE.match(stripped) or LABOR_ONLY.match(stripped):
            flush()
            continue
        if ATTENDANCE_RE.match(stripped):
            flush()
            current = [stripped]
            continue
        if REPORT_LEVEL_RE.match(stripped):
            flush()
            current = [stripped]
            continue
        if _is_item_line(stripped):
            flush()
            current = [stripped]
            continue
        if current and ATTENDANCE_RE.match(current[0]):
            current.append(stripped)
            continue
        if current and (
            TERMINAL_STATUS.match(current[-1]) or REPORT_LEVEL_RE.match(current[0])
        ) and (_looks_like_new_activity(stripped) or REPORT_LEVEL_RE.match(stripped)):
            flush()
            current = [stripped]
            continue
        if current:
            current.append(stripped)
        elif _looks_like_new_activity(stripped) or REPORT_LEVEL_RE.match(stripped):
            current = [stripped]
    flush()
    return _expand_blocks_by_intervals(blocks)


def _interval_led_line(line: str) -> bool:
    found = find_intervals(line)
    if not found:
        return False
    stripped = line.strip()
    raw = found[0].raw.strip()
    return stripped == raw or stripped.lower().startswith(raw[:6].lower())


def _item_only_line(block: list[str]) -> str | None:
    if not block:
        return None
    if _is_item_line(block[0]):
        return _split_item_and_rest(block[0])[0]
    return None


def _expand_blocks_by_intervals(blocks: list[list[str]]) -> list[list[str]]:
    expanded: list[list[str]] = []
    for block in blocks:
        iv_idxs = [i for i, line in enumerate(block) if find_intervals(line)]
        if len(iv_idxs) < 2:
            expanded.append(block)
            continue
        item_only = _item_only_line(block)
        first_iv = iv_idxs[0]
        work_before_first = any(
            0 < idx < first_iv and not find_intervals(block[idx])
            for idx in range(len(block))
        )

        def with_item(chunk: list[str]) -> list[str]:
            if item_only and chunk and not _is_item_line(chunk[0]):
                return [item_only, *chunk]
            return chunk

        if work_before_first:
            prev = 0
            for iv in iv_idxs:
                chunk = block[prev : iv + 1]
                expanded.append(with_item(chunk))
                prev = iv + 1
            if prev < len(block):
                expanded[-1].extend(block[prev:])
        else:
            for n, iv in enumerate(iv_idxs):
                end = iv_idxs[n + 1] if n + 1 < len(iv_idxs) else len(block)
                chunk = block[0:end] if n == 0 else block[iv:end]
                expanded.append(with_item(chunk))
    return expanded


def _retitle_if_operational(item: str, work: str) -> tuple[str, str]:
    blob = f"{item} {work}"
    if FARM_GATES.search(work):
        return "Farm gates", "operational"
    if re.search(r"empty\s+(?:scotch|parts)\s+car", blob, re.I):
        return "Empty scotch/parts car", "operational"
    if REPORT_LEVEL_RE.match(item) or REPORT_LEVEL_RE.match(work.split("\n")[0] if work else ""):
        label = (work or item).split("\n")[0][:60]
        return label, "operational"
    return item, ""


def _interval_for_block(lines: list[str]) -> Interval | None:
    blob = "\n".join(lines)
    found = find_intervals(blob)
    if found:
        return found[0]
    return None


def _classify_state(blob: str) -> str:
    if NOT_TESTED.search(blob):
        return "Not tested"
    if AWAIT_PARTS.search(blob):
        return "Awaiting spares"
    if STILL_REPAIR.search(blob):
        return "Still under repair"
    if re.search(r"\brunning\b", blob, re.I):
        return "Reported operational"
    if re.search(r"\ball in order\b|\ball ok\b|\ball good\b", blob, re.I):
        return "Reported operational"
    if re.search(r"^complete\b|\breported complete\b", blob, re.I | re.M):
        return "Reported complete"
    return "State not established from report"


def _follow_up(blob: str) -> str:
    lines = []
    for line in blob.splitlines():
        if HANDOVER.search(line) or NOT_TESTED.search(line) or AWAIT_PARTS.search(line):
            if not RUNNING.match(line.strip()):
                lines.append(line.strip())
    return "; ".join(lines)


def _people(lines: list[str]) -> str:
    if not lines:
        return ""
    last = lines[-1].strip()
    if REPORT_LEVEL_RE.match(last) or WORK_VERB.match(last) or LABOR_ONLY.match(last):
        return ""
    if PEOPLE_LINE.match(last) and not ITEM_RE.match(last) and not find_intervals(last):
        if not RUNNING.search(last) and not STILL_REPAIR.search(last):
            if len(last.split()) <= 8:
                return last
    return ""


def _work_and_happened(lines: list[str], item: str, interval_raw: str, people: str) -> tuple[str, str]:
    body: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.casefold() == item.casefold():
            continue
        if interval_raw and stripped == interval_raw:
            continue
        if people and stripped == people:
            continue
        if HEADING_RE.match(stripped):
            continue
        # drop isolated time-only line
        if find_intervals(stripped) and len(stripped) < 24:
            continue
        # strip leading interval from an item+time line
        _, rest = _split_item_and_rest(stripped)
        if rest and find_intervals(rest) and rest == stripped[len(item) :].strip(" -–—"):
            leftover = INTERVAL_RE_STRIP.sub("", rest).strip(" -–—")
            if leftover:
                body.append(leftover)
            continue
        cleaned = stripped
        if interval_raw:
            cleaned = cleaned.replace(interval_raw, "").strip(" -–—")
        if cleaned:
            body.append(cleaned)
    if not body:
        return "", ""
    happened = body[0]
    work = " ".join(body)
    return happened, work


INTERVAL_RE_STRIP = re.compile(
    r"(?:sos|eos|eoe|still\s+busy|\d{1,2}\s*[h:.]\s*\d{2}|\d{1,2}\s*h)"
    r"\s*(?:[-–—=]{1,2}|to|tot)\s*"
    r"(?:sos|eos|eoe|still\s+busy|\d{1,2}\s*[h:.]\s*\d{2}|\d{1,2}\s*h)",
    re.I,
)


def _verbatim(blob: str) -> list[str]:
    found: list[str] = []
    for pattern in (
        r"no operator[^\n.]*",
        r"not tested[^\n.]*",
        r"still busy[^\n.]*",
        r"photos? on report group",
        r"night shift to[^\n.]*",
        r"ns to[^\n.]*",
    ):
        for match in re.finditer(pattern, blob, re.I):
            phrase = match.group(0).strip()
            if phrase and phrase not in found:
                found.append(phrase)
    return found


def _attendance_entry(
    block: list[str],
    config: dict[str, Any],
    source_ref: str,
) -> Entry | None:
    heading = block[0].strip()
    body = [line.strip() for line in block[1:] if line.strip()]
    if ALL_AT_WORK.match(heading) and not body:
        return None
    if not body:
        return None
    names = "; ".join(body)
    return Entry(
        item="Attendance",
        item_key=item_key("Attendance", config),
        period_raw="",
        start=None,
        end=None,
        start_kind="missing",
        end_kind="missing",
        what_happened=names,
        work_finding=names,
        last_reported_state="",
        follow_up="",
        people=names,
        work_character="attendance",
        media_present=False,
        source_ref=source_ref,
        verbatim_exceptions=[],
        reported_work_interval="",
    )


def extract_entries(
    unit: ReportingUnit,
    config: dict[str, Any],
    media_from_unit: bool = False,
) -> list[Entry]:
    text = combined_text(unit)
    media = bool(unit.message.media_refs) or any(
        src.media_refs for src in unit.extra_sources
    )
    if PHOTOS.search(text) and "report group" in text.lower():
        media = True
    blocks = split_blocks(text)
    entries: list[Entry] = []
    source_ref = unit.message.source_id
    shift = unit.shift
    for block in blocks:
        if ATTENDANCE_RE.match(block[0]):
            attendance = _attendance_entry(block, config, source_ref)
            if attendance is not None:
                entries.append(attendance)
            continue
        if ITEM_RE.match(block[0]):
            item, first_rest = _split_item_and_rest(block[0])
        else:
            item, first_rest = _item_from_free_text(block[0]), ""
        interval = _interval_for_block(block)
        if interval is None and first_rest:
            found = find_intervals(first_rest)
            interval = found[0] if found else None
        blob = "\n".join(block)
        people = _people(block)
        period_raw = interval.raw if interval else ""
        happened, work = _work_and_happened(block, item, period_raw, people)
        item, operational = _retitle_if_operational(item, work)
        if interval:
            start, end = interval.start, interval.end
            start_kind, end_kind = interval.start_kind, interval.end_kind
        else:
            start = end = None
            start_kind = end_kind = "missing"
            if not period_raw:
                period_raw = "not stated"
        ambiguous = bool(interval and suspicious_numeric_interval(interval, shift))
        duration = reported_work_interval(interval, shift) if interval else ""
        state = _classify_state(blob)
        follow = _follow_up(blob)
        character = operational
        if re.search(r"\bservice\b|\bmaintenance\b", blob, re.I) and not re.search(
            r"\bbreakdown\b", blob, re.I
        ):
            if re.search(r"\bservice\b", blob, re.I):
                character = "service"
        entries.append(
            Entry(
                item=item,
                item_key=item_key(item, config),
                period_raw=period_raw,
                start=start,
                end=end,
                start_kind=start_kind,
                end_kind=end_kind,
                what_happened=happened,
                work_finding=work,
                last_reported_state=state,
                follow_up=follow,
                people=people,
                work_character=character,
                media_present=media and bool(PHOTOS.search(blob) or media_from_unit),
                source_ref=source_ref,
                verbatim_exceptions=_verbatim(blob),
                reported_work_interval=duration,
                downtime_explicit=bool(DOWNTIME.search(blob)),
                interval_ambiguous=ambiguous,
            )
        )
        if media and not entries[-1].media_present and PHOTOS.search(blob):
            entries[-1].media_present = True
    # If the unit has media but no block mentioned it, mark the first entry
    # only when the whole message is media-centric, else pack-level later.
    if media and entries and "photos on report group" in text.lower():
        if not any(entry.media_present for entry in entries):
            entries[0].media_present = True
    return entries


def enforce_postconditions(entries: list[Entry]) -> list[Entry]:
    forbidden_live = re.compile(
        r"\bcurrently running\b|\bmachine is currently\b|\bis currently running\b",
        re.I,
    )
    for entry in entries:
        if entry.start_kind != "numeric" or entry.end_kind != "numeric":
            entry.reported_work_interval = ""
        if entry.last_reported_state.lower() in {"running", "operational"}:
            entry.last_reported_state = "Reported operational"
        if forbidden_live.search(entry.last_reported_state):
            entry.last_reported_state = "Reported operational"
        if entry.last_reported_state == "Running":
            entry.last_reported_state = "Reported operational"
    return entries
