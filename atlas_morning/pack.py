from __future__ import annotations

from datetime import date, datetime
from typing import Any

from atlas_morning.assign import assign_units, missing_shift_flags, select_pack
from atlas_morning.config import merge_aliases
from atlas_morning.extract import enforce_postconditions, extract_entries
from atlas_morning.filter import build_reporting_units, filter_relevant_messages
from atlas_morning.models import Message, Pack
from atlas_morning.reconcile import apply_corrections, flag_entries
from atlas_morning.table import render_pack as render_pack_markdown


def build_pack(
    messages: list[Message],
    config: dict[str, Any],
    operational_day: date,
    *,
    aliases: dict[str, Any] | None = None,
    corrections: dict[str, Any] | None = None,
) -> Pack:
    cfg = merge_aliases(config, aliases or {})
    relevant, sender_flags = filter_relevant_messages(messages, cfg)
    units = build_reporting_units(relevant)
    units = assign_units(units, cfg)
    selected = select_pack(units, operational_day)

    entries = []
    for unit in selected:
        entries.extend(extract_entries(unit, cfg))
    entries = enforce_postconditions(entries)
    entries = flag_entries(entries)
    if corrections:
        entries = apply_corrections(entries, corrections)

    flags = list(sender_flags)
    flags.extend(missing_shift_flags(selected))
    for unit in selected:
        if unit.late:
            flags.append(
                f"Late-posted report kept with described cycle: "
                f"{unit.message.sender} {unit.message.timestamp.isoformat(sep=' ')}"
            )
        if unit.association_uncertain:
            flags.append(
                f"Operational day/shift association uncertain: "
                f"{unit.message.sender} {unit.message.timestamp.isoformat(sep=' ')}"
            )

    return Pack(
        operational_day=operational_day,
        units=selected,
        entries=entries,
        flags=flags,
        loaded_messages=list(messages),
        relevant_messages=relevant,
    )


def render_pack(pack: Pack) -> str:
    return render_pack_markdown(pack)


def infer_operational_day(now: datetime | None = None) -> date:
    """The operational day consumed by the next/current ~05:30 meeting."""
    from atlas_morning.assign import operational_day_containing

    when = now or datetime.now()
    return operational_day_containing(when, start_hour=6)
