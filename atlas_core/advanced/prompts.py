from __future__ import annotations

from collections.abc import Sequence

from atlas_core.capabilities.awareness import CapabilityAwareness

from .intent import InterpretedIntent


ADVANCED_SYSTEM = """You are Atlas in advanced conversation.

You interpret intent and produce a Task Brief.

You can select Atlas capability ids.

You cannot execute work.
You cannot create tasks.
You cannot call tools.
You cannot grant authority.
You cannot send email, run automation, or claim that work completed.

Return one JSON object with keys:
  objective, capabilities, expected_effect, constraints, deliverable_kind, notes

capabilities is a list of Atlas capability ids from the catalog below.
Never use tool names, vendor names, MCP names, or implementation names.
Never include authority_scope, task identifiers, or execution identifiers.
required_authority is derived later from the selected capabilities; do not grant it.
"""


def render_catalog(items: Sequence[CapabilityAwareness]) -> str:
    lines = [
        "Capability catalog (ids only; these are not tools):",
        "",
    ]
    for item in items:
        lines.append(f"- {item.id}: {item.description}")
        lines.append(
            f"  required_authority {item.required_authority}; "
            f"confirmation {item.confirmation}; "
            f"effect class {item.side_effect_class}"
        )
    return "\n".join(lines)


def build_system_prompt(catalog: Sequence[CapabilityAwareness]) -> str:
    return f"{ADVANCED_SYSTEM.rstrip()}\n\n{render_catalog(catalog)}\n"


def build_model_input(intent: InterpretedIntent) -> str:
    lines = [f"Objective: {intent.objective}"]
    if intent.notes:
        lines.append(f"Notes: {intent.notes}")
    lines.append("Produce a Task Brief JSON object, then stop.")
    return "\n".join(lines)
