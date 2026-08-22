from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from atlas_core.authority import validate_authority

from .exposure import CapabilityExposure, ExposurePolicy


ConfirmationRequirement = Literal["none", "required"]
SideEffectClass = Literal["none", "reversible", "irreversible", "external_effect"]


@dataclass(frozen=True)
class CapabilityDefinition:
    """Stable Atlas capability meaning. Not an implementation and not a permission."""

    id: str
    description: str
    required_authority: str
    confirmation: ConfirmationRequirement
    side_effect_class: SideEffectClass

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("capability id must not be empty")
        if not self.description.strip():
            raise ValueError("capability description must not be empty")
        object.__setattr__(self, "required_authority", validate_authority(self.required_authority))
        if self.confirmation not in {"none", "required"}:
            raise ValueError(f"Unsupported confirmation: {self.confirmation}")
        if self.side_effect_class not in {
            "none",
            "reversible",
            "irreversible",
            "external_effect",
        }:
            raise ValueError(f"Unsupported side_effect_class: {self.side_effect_class}")

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "description": self.description,
            "required_authority": self.required_authority,
            "confirmation": self.confirmation,
            "side_effect_class": self.side_effect_class,
        }


def catalog() -> tuple[CapabilityDefinition, ...]:
    """Atlas capability meanings. Existence here does not imply an implementation."""

    return (
        CapabilityDefinition(
            id="reasoning.general",
            description="Explain ideas and answer questions from stated information.",
            required_authority="interpret",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="generation.compose",
            description="Compose requested prose such as explanations or stories in conversation.",
            required_authority="interpret",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="automation.workflow",
            description=(
                "Atlas understands workflow automation as a product capability. "
                "Starting, changing, or running automation is Work, not conversation."
            ),
            required_authority="execute_external",
            confirmation="required",
            side_effect_class="external_effect",
        ),
        CapabilityDefinition(
            id="automation.workflow.create",
            description="Create an automation workflow.",
            required_authority="execute_external",
            confirmation="required",
            side_effect_class="external_effect",
        ),
        CapabilityDefinition(
            id="automation.workflow.execute",
            description="Run an existing automation workflow.",
            required_authority="execute_external",
            confirmation="required",
            side_effect_class="external_effect",
        ),
        CapabilityDefinition(
            id="communication.email.send",
            description="Send an email communication.",
            required_authority="communicate",
            confirmation="required",
            side_effect_class="external_effect",
        ),
        CapabilityDefinition(
            id="planning.general",
            description="General bounded planning and task decomposition.",
            required_authority="interpret",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="reasoning.deep_analysis",
            description="Deep evidence-led reasoning for complex trade-offs and failure modes.",
            required_authority="interpret",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="coding.software_engineering",
            description="Bounded software implementation and code maintenance work.",
            required_authority="modify_internal",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="documents.multimodal",
            description="Interpret multimodal document content when deterministic extraction is insufficient.",
            required_authority="interpret",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="knowledge.ingest_text",
            description="Persist and chunk extracted text into Atlas full-text knowledge with provenance.",
            required_authority="modify_internal",
            confirmation="none",
            side_effect_class="reversible",
        ),
        CapabilityDefinition(
            id="knowledge.search",
            description=(
                "Retrieve source-grounded chunks from Atlas's ingested local knowledge corpus only. "
                "This is not web search and not general-world trivia."
            ),
            required_authority="read",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="files.list",
            description="List one bounded page of a configured local directory.",
            required_authority="read",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="files.stat",
            description="Observe metadata for a configured local source without following symlinks.",
            required_authority="read",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="files.hash",
            description="Compute a stable exact-byte SHA-256 for a configured local regular file.",
            required_authority="read",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="files.read",
            description="Acquire complete bounded UTF-8 text from a configured local regular file.",
            required_authority="read",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="knowledge.answer",
            description=(
                "Compose a source-grounded answer from a knowledge_search_results artifact "
                "without adding claims. Requires a prior knowledge.search step. "
                "Not for general questions that lack ingested local sources."
            ),
            required_authority="read",
            confirmation="none",
            side_effect_class="none",
        ),
        CapabilityDefinition(
            id="operations.morning_pack.generate",
            description="Generate the frozen V1 TMM morning pack from a configured source.",
            required_authority="read",
            confirmation="none",
            side_effect_class="none",
        ),
    )


def lookup(capability_id: str) -> CapabilityDefinition | None:
    return next((item for item in catalog() if item.id == capability_id), None)


def require(capability_id: str) -> CapabilityDefinition:
    found = lookup(capability_id)
    if found is None:
        raise ValueError(f"Unknown capability: {capability_id}")
    return found


def default_exposure() -> ExposurePolicy:
    policy = ExposurePolicy()
    for row in (
        CapabilityExposure(
            "reasoning.general",
            chat="explain",
            advanced_conversation="hidden",
            work="hidden",
        ),
        CapabilityExposure(
            "generation.compose",
            chat="explain",
            advanced_conversation="hidden",
            work="hidden",
        ),
        CapabilityExposure(
            "automation.workflow",
            chat="explain",
            advanced_conversation="hidden",
            work="hidden",
        ),
        CapabilityExposure(
            "automation.workflow.create",
            chat="explain",
            advanced_conversation="brief",
            work="execute",
        ),
        CapabilityExposure(
            "automation.workflow.execute",
            chat="explain",
            advanced_conversation="brief",
            work="execute",
        ),
        CapabilityExposure(
            "communication.email.send",
            chat="explain",
            advanced_conversation="brief",
            work="execute",
        ),
        CapabilityExposure(
            "knowledge.ingest_text",
            chat="explain",
            advanced_conversation="brief",
            work="execute",
        ),
    ):
        policy.declare(row)
    return policy


def brief_catalog(
    *,
    policy: ExposurePolicy | None = None,
) -> tuple[CapabilityDefinition, ...]:
    """Meanings Advanced may propose. Not tools, not handlers."""

    exposure = policy or default_exposure()
    return tuple(
        item
        for item in catalog()
        if exposure.get(item.id).for_mode("ADVANCED_CONVERSATION") == "brief"
    )
