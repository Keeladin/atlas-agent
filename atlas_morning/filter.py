from __future__ import annotations

import re
from typing import Any

from atlas_morning.config import canonical_sender, sender_is_relevant, sender_is_user
from atlas_morning.models import Message, ReportingUnit

HEADING_RE = re.compile(
    r"^(?:tmm\s+)?(?:night|day)\s+shift(?:\s+tmm)?\b|"
    r"^night\s+shift\s+tmm\b|"
    r"^tmm\s+night\b|"
    r"^(?:daily|dialy)\s+report\b",
    re.I,
)
ITEM_RE = re.compile(
    r"^("
    r"[A-Za-z]{2,10}(?:[\s\-]gt)?"
    r"[\s\-]*\d{1,4}[A-Za-z]?"
    r"|L\s*\d{2,4}[A-Za-z]?"
    r")\b",
    re.I,
)
# Words that start a quantity/prose line, not an equipment identity.
PROSE_ITEM_PREFIXES = frozenset(
    {
        "replace",
        "repair",
        "remove",
        "removed",
        "fit",
        "fitt",
        "weld",
        "install",
        "collect",
        "collected",
        "drain",
        "drill",
        "dump",
        "empty",
        "fasten",
        "find",
        "found",
        "burst",
        "load",
        "need",
        "needs",
        "only",
        "ordered",
        "strip",
        "from",
        "in",
        "on",
        "at",
        "of",
        "to",
        "for",
        "cut",
        "clear",
        "check",
        "do",
        "got",
        "took",
        "made",
        "start",
        "started",
        "went",
        "fetch",
        "fetched",
        "assist",
        "jumpstart",
        "service",
        "recover",
        "transport",
        "workforce",
        "labour",
        "labor",
        "filters",
        "bay",
        "rocky",
        "pomp",
        "pump",
        "rod",
        "precussion",
        "collering",
    }
)


def is_plausible_equipment_id(token: str) -> bool:
    raw = token.strip()
    if not raw:
        return False
    if re.fullmatch(r"L\s*\d{2,4}[A-Za-z]?", raw, re.I):
        return True
    match = re.fullmatch(
        r"([A-Za-z]{2,10})(?:[\s\-]gt)?[\s\-]*(\d{1,4})([A-Za-z]+)?",
        raw,
        re.I,
    )
    if not match:
        return False
    prefix = match.group(1).lower()
    suffix = (match.group(3) or "").lower()
    if prefix in PROSE_ITEM_PREFIXES:
        return False
    if suffix in {"x", "m"}:
        return False
    return True
TIME_HINT_RE = re.compile(
    r"\b(?:sos|eos|eoe|still\s+busy|"
    r"\d{1,2}\s*[h:.]\s*\d{2}\s*[-–—=]{1,2}\s*"
    r"(?:\d{1,2}\s*[h:.]\s*\d{2}|sos|eos|eoe|still\s+busy))\b",
    re.I,
)
ATTENDANCE_RE = re.compile(
    r"^(absent|absend|all at work|absent for shift|work\s*force|workforce)\b",
    re.I,
)


def looks_like_reporting_unit(message: Message) -> bool:
    text = message.text.strip()
    if not text:
        return False
    if message.media_refs and len(text) < 80 and not TIME_HINT_RE.search(text):
        # Voice/photo-only follow-ups are not shift reports.
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    if any(HEADING_RE.match(line) for line in lines[:4]):
        return True
    if any(
        ITEM_RE.match(line) and is_plausible_equipment_id(ITEM_RE.match(line).group(1))
        for line in lines
    ):
        return True
    if TIME_HINT_RE.search(text):
        return True
    if any(ATTENDANCE_RE.match(line) for line in lines):
        return True
    return False


def is_chatter(message: Message) -> bool:
    if looks_like_reporting_unit(message):
        return False
    return len(message.text.strip()) < 80


def filter_relevant_messages(
    messages: list[Message],
    config: dict[str, Any],
) -> tuple[list[Message], list[str]]:
    """Keep relevant-sender messages. User messages stay in loaded source only."""
    kept: list[Message] = []
    flags: list[str] = []
    for message in messages:
        if sender_is_user(message.sender, config):
            continue
        if sender_is_relevant(message.sender, message.timestamp, config):
            kept.append(message)
            continue
        # Do not guess unknown labels into a supervisor.
        if looks_like_reporting_unit(message) and _maybe_supervisor_label(
            message.sender, config
        ):
            flags.append(
                f"Sender/role uncertain: {message.sender!r} "
                f"at {message.timestamp.isoformat(sep=' ')} — not guessed"
            )
    return kept, flags


def _maybe_supervisor_label(label: str, config: dict[str, Any]) -> bool:
    compact = "".join(ch for ch in label.lower() if ch.isalnum())
    if len(compact) < 3:
        return False
    for sender in config.get("relevant_senders") or []:
        names = [sender["name"], *(sender.get("aliases") or [])]
        for name in names:
            name_c = "".join(ch for ch in name.lower() if ch.isalnum())
            if name_c and (name_c in compact or compact in name_c) and name_c != compact:
                return True
    return False


def build_reporting_units(messages: list[Message]) -> list[ReportingUnit]:
    units: list[ReportingUnit] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if not looks_like_reporting_unit(message):
            i += 1
            continue
        unit = ReportingUnit(message=message)
        i += 1
        while i < len(messages):
            nxt = messages[i]
            if nxt.sender != message.sender:
                break
            gap = (nxt.timestamp - message.timestamp).total_seconds()
            if gap < 0 or gap > 45 * 60:
                break
            if (
                nxt.media_refs
                and len(nxt.text.strip()) < 80
                and not looks_like_reporting_unit(nxt)
            ):
                unit.extra_sources.append(nxt)
                i += 1
                continue
            if not looks_like_reporting_unit(nxt):
                # Chatter / orphan comments stay out of the previous report.
                i += 1
                continue
            if HEADING_RE.search(nxt.text) or (
                TIME_HINT_RE.search(nxt.text) and ITEM_RE.search(nxt.text)
            ):
                break
            # Short addendum that itself looks like a report: own unit.
            break
        units.append(unit)
    return units


def combined_text(unit: ReportingUnit) -> str:
    parts = [unit.message.text]
    for src in unit.extra_sources:
        if src.media_refs and len(src.text.strip()) < 80:
            continue
        parts.append(src.text)
    return "\n".join(parts)


def display_sender(message: Message, config: dict[str, Any]) -> str:
    return canonical_sender(message.sender, config)
