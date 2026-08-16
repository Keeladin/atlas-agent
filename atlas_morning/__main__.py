from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from atlas_morning.config import load_aliases, load_config, save_aliases
from atlas_morning.load import load_messages
from atlas_morning.pack import build_pack, infer_operational_day, render_pack
from atlas_morning.reconcile import load_corrections


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the V1 morning engineering table from a WhatsApp export."
    )
    parser.add_argument("input", help="WhatsApp export .txt or .zip")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[1] / "config" / "v1.json"),
    )
    parser.add_argument("--aliases", default=None, help="Persistent aliases JSON")
    parser.add_argument("--corrections", default=None, help="This-pack corrections JSON")
    parser.add_argument(
        "--day",
        default=None,
        help="Operational day (YYYY-MM-DD of the 06:00 start). Default: containing now.",
    )
    parser.add_argument("--output", default=None, help="Write Markdown here")
    parser.add_argument(
        "--add-alias",
        action="append",
        default=[],
        metavar="FROM=TO",
        help="Persist an item alias (repeatable). Does not build a pack.",
    )
    args = parser.parse_args()

    if args.add_alias:
        if not args.aliases:
            parser.error("--add-alias requires --aliases")
        aliases = load_aliases(args.aliases)
        for item in args.add_alias:
            if "=" not in item:
                parser.error(f"Alias must be FROM=TO, got {item!r}")
            source, target = item.split("=", 1)
            aliases.setdefault("item_aliases", {})[source.strip()] = target.strip()
        save_aliases(args.aliases, aliases)
        print(f"Wrote aliases to {args.aliases}")
        return

    config = load_config(args.config)
    aliases = load_aliases(args.aliases) if args.aliases else {}
    corrections = load_corrections(args.corrections) if args.corrections else {}
    messages = load_messages(args.input)
    if args.day:
        op_day = date.fromisoformat(args.day)
    else:
        op_day = infer_operational_day()

    pack = build_pack(
        messages,
        config,
        op_day,
        aliases=aliases,
        corrections=corrections,
    )
    text = render_pack(pack)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
