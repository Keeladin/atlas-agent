from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any


def load_config(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.setdefault("item_aliases", {})
    data.setdefault("sender_aliases", {})
    data.setdefault("excluded_v1_authors", [])
    return data


def save_config(path: str | Path, config: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_aliases(path: str | Path | None) -> dict[str, Any]:
    if path is None or not Path(path).exists():
        return {"item_aliases": {}, "sender_aliases": {}}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.setdefault("item_aliases", {})
    data.setdefault("sender_aliases", {})
    return data


def save_aliases(path: str | Path, aliases: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(aliases, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def merge_aliases(config: dict[str, Any], aliases: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(config)
    items = dict(merged.get("item_aliases") or {})
    items.update(aliases.get("item_aliases") or {})
    senders = dict(merged.get("sender_aliases") or {})
    senders.update(aliases.get("sender_aliases") or {})
    merged["item_aliases"] = items
    merged["sender_aliases"] = senders
    return merged


def _parse_bound(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def canonical_sender(label: str, config: dict[str, Any]) -> str:
    raw = label.strip()
    aliases = config.get("sender_aliases") or {}
    if raw in aliases:
        return str(aliases[raw])
    lowered = raw.casefold()
    for sender in config.get("relevant_senders") or []:
        names = [sender["name"], *(sender.get("aliases") or [])]
        if any(name.casefold() == lowered for name in names):
            return str(sender["name"])
    return raw


def sender_is_relevant(label: str, when: datetime, config: dict[str, Any]) -> bool:
    name = canonical_sender(label, config)
    day = when.date()
    for sender in config.get("relevant_senders") or []:
        names = [sender["name"], *(sender.get("aliases") or [])]
        if name not in names and name.casefold() not in {n.casefold() for n in names}:
            continue
        start = _parse_bound(sender.get("from"))
        end = _parse_bound(sender.get("until"))
        if start and day < start:
            return False
        if end and day > end:
            return False
        return True
    return False


def sender_is_user(label: str, config: dict[str, Any]) -> bool:
    name = canonical_sender(label, config)
    excluded = config.get("excluded_v1_authors") or []
    return any(name.casefold() == item.casefold() for item in excluded)


def item_key(raw: str, config: dict[str, Any]) -> str:
    compact = "".join(ch for ch in raw.upper() if ch.isalnum())
    aliases = config.get("item_aliases") or {}
    if raw in aliases:
        return str(aliases[raw])
    if compact in aliases:
        return str(aliases[compact])
    for source, target in aliases.items():
        source_compact = "".join(ch for ch in str(source).upper() if ch.isalnum())
        if source_compact == compact:
            return str(target)
    return compact or raw.strip()
