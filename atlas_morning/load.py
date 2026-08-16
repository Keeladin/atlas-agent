from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

from atlas_morning.models import Message

HEADER_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(.*)$"
)
SENDER_RE = re.compile(r"^([^:]+):\s*(.*)$", re.S)
ATTACHED_RE = re.compile(
    r"((?:IMG|PTT|VID|STK|AUD|DOC)-[\w-]+\.(?:jpg|jpeg|png|gif|mp4|opus|pdf|webp))",
    re.I,
)


def _parse_timestamp(date_s: str, time_s: str) -> datetime:
    time_s = time_s.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%y %H:%M"):
        try:
            return datetime.strptime(f"{date_s} {time_s}", fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised WhatsApp timestamp: {date_s} {time_s}")


def media_refs_from_text(text: str) -> tuple[str, ...]:
    refs: list[str] = []
    if "<Media omitted>" in text or "<media omitted>" in text.lower():
        refs.append("<Media omitted>")
    refs.extend(ATTACHED_RE.findall(text))
    if re.search(r"\bphotos?\b", text, re.I) and "photos on report group" in text.lower():
        refs.append("photos on report group")
    elif re.search(r"photos? on report group", text, re.I):
        refs.append("photos on report group")
    if "(file attached)" in text.lower() and not refs:
        refs.append("file attached")
    # preserve order, unique
    seen: set[str] = set()
    out: list[str] = []
    for item in refs:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return tuple(out)


def parse_whatsapp_text(text: str) -> list[Message]:
    messages: list[Message] = []
    current: dict[str, object] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        body = str(current["text"])
        messages.append(
            Message(
                sender=str(current["sender"]),
                timestamp=current["timestamp"],  # type: ignore[arg-type]
                text=body,
                media_refs=media_refs_from_text(body),
            )
        )
        current = None

    for line in text.splitlines():
        header = HEADER_RE.match(line)
        if header:
            rest = header.group(3)
            sender_match = SENDER_RE.match(rest)
            if sender_match:
                flush()
                current = {
                    "sender": sender_match.group(1).strip(),
                    "timestamp": _parse_timestamp(header.group(1), header.group(2)),
                    "text": sender_match.group(2),
                }
                continue
            # system / non-sender line
            if current is not None:
                current["text"] = str(current["text"]) + "\n" + line
            continue
        if current is not None:
            current["text"] = str(current["text"]) + "\n" + line
    flush()
    return messages


def load_messages(path: str | Path) -> list[Message]:
    source = Path(path)
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".txt") and not name.endswith("/")
            ]
            if not names:
                raise ValueError(f"No chat .txt in {source}")
            text = archive.read(names[0]).decode("utf-8", errors="replace")
        return parse_whatsapp_text(text)
    return parse_whatsapp_text(source.read_text(encoding="utf-8"))
