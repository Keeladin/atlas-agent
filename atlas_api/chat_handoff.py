from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from atlas_core.chat import ChatRuntime
from atlas_core.chat.conversations import ConversationView


# Advanced compiles a small JSON brief. Constraints usually sit in the last
# few exchanges. Sixteen turns / 8_000 characters keeps those without dumping
# an unbounded Chat archive into the compiler.
MAX_HANDOFF_CONTEXT_TURNS = 16
MAX_HANDOFF_CONTEXT_CHARS = 8_000

_HANDOFF_UTTERANCE = re.compile(
    r"^(please\s+)?("
    r"(submit|send|start)\s+(this\s+)?(as\s+)?work|"
    r"review(\s+it)?\s+in\s+work"
    r")(\s+please)?\s*[.!?]*$",
    re.IGNORECASE,
)


class ChatHandoffError(ValueError):
    """Invalid conversation handoff. Not an Advanced or Work failure."""


@dataclass(frozen=True)
class FoldedChatIntent:
    objective: str
    notes: str | None
    conversation_id: str
    until_turn_id: str | None

    def source(self) -> dict[str, str | None]:
        return {
            "conversation_id": self.conversation_id,
            "until_turn_id": self.until_turn_id,
        }


def fold_conversation_intent(
    turns: Sequence[Any],
    *,
    conversation_id: str,
    until_turn_id: str | None = None,
    revision: str | None = None,
) -> FoldedChatIntent:
    """Derive Advanced objective/notes from Chat turns. Does not persist."""

    cid = str(conversation_id or "").strip()
    if not cid:
        raise ChatHandoffError("conversation_id is required")
    selected = _turns_through(turns, until_turn_id)
    objective_turn = _last_user_turn(selected)
    if objective_turn is None:
        raise ChatHandoffError("no user request in the selected conversation range")
    earlier = _turns_before(selected, objective_turn)
    notes = _bound_notes(earlier)
    revised = str(revision or "").strip()
    objective = revised or str(objective_turn.content or "").strip()
    if not objective:
        raise ChatHandoffError("no user request in the selected conversation range")
    boundary = None if until_turn_id is None else str(until_turn_id).strip() or None
    return FoldedChatIntent(
        objective=objective,
        notes=notes,
        conversation_id=cid,
        until_turn_id=boundary,
    )


def intent_from_conversation(
    chat: ChatRuntime,
    *,
    conversation_id: str,
    until_turn_id: str | None = None,
    revision: str | None = None,
) -> FoldedChatIntent:
    view = chat.conversation(str(conversation_id or "").strip())
    return fold_conversation_intent(
        view.turns,
        conversation_id=view.id,
        until_turn_id=until_turn_id,
        revision=revision,
    )


def _turns_through(
    turns: Sequence[Any], until_turn_id: str | None
) -> tuple[Any, ...]:
    items = tuple(turns)
    boundary = None if until_turn_id is None else str(until_turn_id).strip()
    if not boundary:
        return items
    selected: list[Any] = []
    for turn in items:
        selected.append(turn)
        if str(getattr(turn, "id", "") or "") == boundary:
            return tuple(selected)
    raise ChatHandoffError(f"Unknown conversation turn: {boundary}")


def is_handoff_utterance(content: str) -> bool:
    """True when the user is asking to hand off, not stating the Work request."""

    return bool(_HANDOFF_UTTERANCE.match(str(content or "").strip()))


def _last_user_turn(turns: Sequence[Any]) -> Any | None:
    for turn in reversed(tuple(turns)):
        if str(getattr(turn, "role", "") or "") != "user":
            continue
        content = str(getattr(turn, "content", "") or "").strip()
        if content and not is_handoff_utterance(content):
            return turn
    return None


def _turns_before(turns: Sequence[Any], objective_turn: Any) -> tuple[Any, ...]:
    earlier: list[Any] = []
    target = getattr(objective_turn, "id", None)
    for turn in turns:
        if target is not None and getattr(turn, "id", None) == target:
            break
        if turn is objective_turn:
            break
        earlier.append(turn)
    return tuple(earlier)


def _bound_notes(earlier: Sequence[Any]) -> str | None:
    if not earlier:
        return None
    kept: list[str] = []
    chars = 0
    for turn in reversed(tuple(earlier)):
        if len(kept) >= MAX_HANDOFF_CONTEXT_TURNS:
            break
        remaining = MAX_HANDOFF_CONTEXT_CHARS - chars
        if remaining <= 0:
            break
        line = _format_turn(turn)
        if len(line) > remaining:
            line = "…" + line[-remaining:].lstrip()
        kept.append(line)
        chars += len(line) + 1
    if not kept:
        return None
    kept.reverse()
    return "\n".join(kept)


def _format_turn(turn: Any) -> str:
    speaker = "User" if str(getattr(turn, "role", "") or "") == "user" else "Atlas"
    content = str(getattr(turn, "content", "") or "").strip()
    return f"{speaker}: {content}"


def conversation_snapshot(view: ConversationView) -> dict[str, Any]:
    """Comparable Chat persistence fingerprint. Not a handoff record."""

    return {
        "id": view.id,
        "updated_at": view.updated_at,
        "turn_count": view.turn_count,
        "turn_ids": [turn.id for turn in view.turns],
        "turns": [
            {
                "id": turn.id,
                "role": turn.role,
                "content": turn.content,
            }
            for turn in view.turns
        ],
    }


def is_unknown_conversation(exc: BaseException) -> bool:
    return isinstance(exc, ValueError) and str(exc).startswith("Unknown conversation:")
