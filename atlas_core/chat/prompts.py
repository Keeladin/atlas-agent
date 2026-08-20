from __future__ import annotations

from collections.abc import Sequence

from .awareness import CapabilityAwareness
from .conversations import ConversationTurn


CHAT_SYSTEM = """You are Atlas in conversation.

You can explain capabilities.

You cannot execute work.
You cannot create tasks.
You cannot call tools.
You cannot claim work was completed.

If the user wants something executed, explain that it requires a Work request.
"""


def render_awareness(items: Sequence[CapabilityAwareness]) -> str:
    if not items:
        return "You may explain Atlas capabilities in general terms. You have no tools."
    lines = [
        "You may explain the following Atlas capabilities.",
        "These are meanings, not tools. You cannot invoke them.",
        "",
    ]
    for item in items:
        lines.append(f"- {item.id}: {item.description}")
        lines.append(
            f"  authority {item.required_authority}; "
            f"confirmation {item.confirmation}; "
            f"effect {item.side_effect_class}"
        )
    return "\n".join(lines)


def build_system_prompt(awareness: Sequence[CapabilityAwareness]) -> str:
    return f"{CHAT_SYSTEM.rstrip()}\n\n{render_awareness(awareness)}\n"


def build_model_input(turns: Sequence[ConversationTurn]) -> str:
    if not turns:
        raise ValueError("Chat generation requires at least one turn.")
    lines: list[str] = []
    for turn in turns:
        speaker = "User" if turn.role == "user" else "Atlas"
        lines.append(f"{speaker}: {turn.content}")
    return "\n".join(lines)
