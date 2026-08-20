from __future__ import annotations

from atlas_core.capabilities.awareness import CapabilityAwareness, catalog

__all__ = ["CapabilityAwareness", "explain_manifest"]

_EXPLAIN_IDS = (
    "reasoning.general",
    "generation.compose",
    "automation.workflow",
)


def explain_manifest() -> tuple[CapabilityAwareness, ...]:
    """Product meanings Chat may explain. Not a tool list and not a Work registry."""

    index = {item.id: item for item in catalog()}
    return tuple(index[item_id] for item_id in _EXPLAIN_IDS)
