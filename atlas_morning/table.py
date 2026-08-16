from __future__ import annotations

from collections import defaultdict

from atlas_morning.models import Entry, Pack, ReportingUnit


def _period_cell(entry: Entry) -> str:
    if entry.reported_work_interval:
        label = entry.reported_work_interval
        kind = "reported work/activity interval"
        if entry.downtime_explicit:
            kind = "reported work/activity interval (downtime wording present in source)"
        return f"{entry.period_raw} ({label}; {kind})"
    return entry.period_raw or "not stated"


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_table(entries: list[Entry]) -> str:
    grouped: dict[str, list[Entry]] = defaultdict(list)
    order: list[str] = []
    for entry in entries:
        key = entry.item_key or entry.item
        if key not in grouped:
            order.append(key)
        grouped[key].append(entry)

    lines = [
        "| Machine / Item | Reported period | What happened | Work / finding | Last reported state | Follow-up / unresolved |",
        "|---|---|---|---|---|---|",
    ]
    for key in order:
        for entry in grouped[key]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape(entry.item),
                        _escape(_period_cell(entry)),
                        _escape(entry.what_happened),
                        _escape(entry.work_finding),
                        _escape(entry.last_reported_state or "—"),
                        _escape(entry.follow_up),
                    ]
                )
                + " |"
            )
    return "\n".join(lines)


def render_exceptions(pack: Pack) -> str:
    lines = ["## Exceptions"]
    if not pack.flags and not any(entry.flags for entry in pack.entries):
        lines.append("None.")
        return "\n".join(lines)
    for flag in pack.flags:
        lines.append(f"- {flag}")
    for entry in pack.entries:
        for flag in entry.flags:
            lines.append(f"- {entry.item}: {flag}")
    return "\n".join(lines)


def render_sources(units: list[ReportingUnit]) -> str:
    lines = ["## Raw reports"]
    for unit in units:
        late = " (late)" if unit.late else ""
        uncertain = " (shift/day association uncertain)" if unit.association_uncertain else ""
        shift = unit.shift
        lines.append(
            f"### {unit.message.sender} {unit.message.timestamp.isoformat(sep=' ')} "
            f"— {shift}{late}{uncertain}"
        )
        lines.append("```")
        lines.append(unit.message.text.rstrip())
        lines.append("```")
        for extra in unit.extra_sources:
            lines.append(f"_Follow-up {extra.timestamp.isoformat(sep=' ')}:_")
            lines.append("```")
            lines.append(extra.text.rstrip())
            lines.append("```")
        refs = list(unit.message.media_refs)
        for extra in unit.extra_sources:
            refs.extend(extra.media_refs)
        if refs:
            lines.append("Media (not interpreted): " + ", ".join(refs))
    return "\n".join(lines)


def render_pack(pack: Pack) -> str:
    start = pack.operational_day.isoformat()
    end_day = pack.operational_day
    from datetime import timedelta

    end = (end_day + timedelta(days=1)).isoformat()
    senders = sorted({unit.message.sender for unit in pack.units})
    header = [
        f"# Morning engineering picture",
        "",
        f"Operational day: {start} 06:00 → {end} 05:59",
        f"Meeting (~05:30) is consumption time, not the cycle boundary.",
        f"Senders: {', '.join(senders) if senders else '(none)'}",
        f"Reporting units: {len(pack.units)}",
        "",
        render_table(pack.entries),
        "",
        render_exceptions(pack),
        "",
        render_sources(pack.units),
    ]
    return "\n".join(header) + "\n"
