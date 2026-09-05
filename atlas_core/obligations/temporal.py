from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_RELATIVE = re.compile(
    r"\b(?:in|within)\s+(\d+)\s*(second|seconds|minute|minutes|hour|hours|day|days|week|weeks)\b",
    re.IGNORECASE,
)
_ISO = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))\b"
)


def _anchor(value: str) -> datetime:
    text = str(value or "").strip().replace(" ", "T", 1)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_satisfiable_until(
    temporal_excerpt: str | None, *, anchor_at: str
) -> tuple[str | None, str | None, str | None]:
    """Normalize only temporal bounds whose meaning is deterministic without guessed locale/timezone.

    Relative durations are timezone-independent. Absolute timestamps must carry an
    explicit offset (or Z). Ambiguous phrases such as "tomorrow" or "by 5pm" are
    intentionally left unresolved so intake can fail closed rather than inventing
    an owner timezone.
    """
    excerpt = str(temporal_excerpt or "").strip()
    if not excerpt:
        return None, None, None
    anchor = _anchor(anchor_at)
    match = _RELATIVE.search(excerpt)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).casefold()
        if unit.startswith("second"):
            delta = timedelta(seconds=amount)
        elif unit.startswith("minute"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("hour"):
            delta = timedelta(hours=amount)
        elif unit.startswith("day"):
            delta = timedelta(days=amount)
        else:
            delta = timedelta(weeks=amount)
        return (anchor + delta).isoformat(), anchor.isoformat(), "UTC"
    match = _ISO.search(excerpt)
    if match:
        parsed = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat(), anchor.isoformat(), str(parsed.tzinfo)
    return None, anchor.isoformat(), None
