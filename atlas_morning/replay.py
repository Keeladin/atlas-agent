"""Temporary historical replay of V1 over a WhatsApp export.

Validation only. Uses the existing pack workflow one operational day at a time.
"""

from __future__ import annotations

import argparse
import traceback
from datetime import date
from pathlib import Path
from typing import Any

from atlas_morning.assign import assign_units
from atlas_morning.config import load_aliases, load_config, merge_aliases
from atlas_morning.filter import build_reporting_units, filter_relevant_messages
from atlas_morning.load import load_messages
from atlas_morning.models import Message
from atlas_morning.pack import build_pack, render_pack


def operational_days_in_corpus(
    messages: list[Message],
    config: dict[str, Any],
) -> list[date]:
    relevant, _ = filter_relevant_messages(messages, config)
    units = assign_units(build_reporting_units(relevant), config)
    days = {unit.operational_day for unit in units if unit.operational_day is not None}
    return sorted(days)


def replay_export(
    input_path: str | Path,
    outdir: str | Path,
    *,
    config_path: str | Path,
    aliases_path: str | Path | None = None,
) -> dict[str, Any]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    packs_dir = out / "packs"
    packs_dir.mkdir(exist_ok=True)
    log_path = out / "replay-log.md"

    messages = load_messages(input_path)
    config = merge_aliases(
        load_config(config_path),
        load_aliases(aliases_path) if aliases_path else {},
    )
    days = operational_days_in_corpus(messages, config)

    lines = [
        "# V1 historical replay log",
        "",
        f"Source: `{input_path}`",
        f"Messages loaded: {len(messages)}",
        f"Operational days with relevant reports: {len(days)}",
        "",
        "| Operational day | Units | Entries | Exceptions | Status |",
        "|---|---:|---:|---:|---|",
    ]
    detail: list[str] = ["", "## Exceptions and errors", ""]

    ok = 0
    failed = 0
    for day in days:
        try:
            pack = build_pack(messages, config, day)
            (packs_dir / f"morning-{day.isoformat()}.md").write_text(
                render_pack(pack),
                encoding="utf-8",
            )
            n_flags = len(pack.flags) + sum(len(entry.flags) for entry in pack.entries)
            lines.append(
                f"| {day.isoformat()} | {len(pack.units)} | {len(pack.entries)} "
                f"| {n_flags} | ok |"
            )
            if pack.flags or any(entry.flags for entry in pack.entries):
                detail.append(f"### {day.isoformat()}")
                for flag in pack.flags:
                    detail.append(f"- {flag}")
                for entry in pack.entries:
                    for flag in entry.flags:
                        detail.append(f"- {entry.item}: {flag}")
                detail.append("")
            ok += 1
        except Exception as exc:
            failed += 1
            lines.append(
                f"| {day.isoformat()} | — | — | — | error: {type(exc).__name__} |"
            )
            detail.append(f"### {day.isoformat()} — ERROR")
            detail.append("```")
            detail.append(traceback.format_exc())
            detail.append("```")
            detail.append("")

    lines.append("")
    lines.append(f"Finished: {ok} ok, {failed} failed, {len(days)} days.")
    log_path.write_text("\n".join(lines + detail) + "\n", encoding="utf-8")
    return {"days": len(days), "ok": ok, "failed": failed, "log": str(log_path)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay V1 over every operational day in a WhatsApp export."
    )
    parser.add_argument("input", help="WhatsApp export .txt or .zip")
    parser.add_argument(
        "--outdir",
        default=str(Path(__file__).resolve().parents[1] / "output" / "replay"),
        help="Directory for packs and replay-log.md",
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[1] / "config" / "v1.json"),
    )
    parser.add_argument("--aliases", default=None)
    args = parser.parse_args()
    result = replay_export(
        args.input,
        args.outdir,
        config_path=args.config,
        aliases_path=args.aliases,
    )
    print(
        f"Replay {result['ok']}/{result['days']} days ok, "
        f"{result['failed']} failed. Log: {result['log']}"
    )


if __name__ == "__main__":
    main()
