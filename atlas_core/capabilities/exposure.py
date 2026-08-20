from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


InteractionMode = Literal["CHAT", "ADVANCED_CONVERSATION", "WORK"]
ExposureKind = Literal["explain", "brief", "execute", "hidden"]

_VALID_EXPOSURE = {"explain", "brief", "execute", "hidden"}
_VALID_MODES = {"CHAT", "ADVANCED_CONVERSATION", "WORK"}


@dataclass(frozen=True)
class CapabilityExposure:
    """Per-mode exposure for one Atlas capability.

    Placeholder policy data only. Nothing in the runtime enforces this yet.
    CHAT/ADVANCED/WORK wiring is intentionally absent.
    """

    capability_id: str
    chat: ExposureKind = "hidden"
    advanced_conversation: ExposureKind = "hidden"
    work: ExposureKind = "hidden"

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be empty")
        for name, value in (
            ("CHAT", self.chat),
            ("ADVANCED_CONVERSATION", self.advanced_conversation),
            ("WORK", self.work),
        ):
            if value not in _VALID_EXPOSURE:
                raise ValueError(f"Unsupported {name} exposure: {value}")

    def for_mode(self, mode: InteractionMode) -> ExposureKind:
        if mode not in _VALID_MODES:
            raise ValueError(f"Unsupported interaction mode: {mode}")
        if mode == "CHAT":
            return self.chat
        if mode == "ADVANCED_CONVERSATION":
            return self.advanced_conversation
        return self.work

    def as_dict(self) -> dict[str, dict[str, str]]:
        return {
            "CHAT": {"exposure": self.chat},
            "ADVANCED_CONVERSATION": {"exposure": self.advanced_conversation},
            "WORK": {"exposure": self.work},
        }


class ExposurePolicy:
    """In-memory exposure table. Not persisted and not enforced."""

    def __init__(self) -> None:
        self._rows: dict[str, CapabilityExposure] = {}

    def declare(self, exposure: CapabilityExposure) -> None:
        self._rows[exposure.capability_id] = exposure

    def get(self, capability_id: str) -> CapabilityExposure:
        return self._rows.get(
            capability_id,
            CapabilityExposure(capability_id),
        )

    def as_dict(self) -> dict[str, dict[str, dict[str, str]]]:
        return {key: row.as_dict() for key, row in sorted(self._rows.items())}
