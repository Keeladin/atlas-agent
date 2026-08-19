from __future__ import annotations

from .contracts import CapabilitySpec, ExecutionBudget
from .registry import CapabilityRegistry


def register_intelligence_capabilities(registry: CapabilityRegistry) -> None:
    specs = (
        CapabilitySpec(
            id="planning.general",
            description="General bounded planning and task decomposition.",
            executor_kind="model",
            required_authority="interpret",
            context_profile="plan",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=48_000, max_output_chars=24_000),
        ),
        CapabilitySpec(
            id="reasoning.general",
            description="General deliberate reasoning over bounded task evidence.",
            executor_kind="model",
            required_authority="interpret",
            context_profile="research",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=64_000, max_output_chars=32_000),
        ),
        CapabilitySpec(
            id="generation.compose",
            description=(
                "Compose the user-requested artifact itself "
                "(story, letter, document, or other requested text). "
                "Do not analyze the request."
            ),
            executor_kind="model",
            required_authority="interpret",
            context_profile="compose",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=48_000, max_output_chars=32_000),
        ),
        CapabilitySpec(
            id="reasoning.deep_analysis",
            description="Deep evidence-led reasoning for complex trade-offs and failure modes.",
            executor_kind="model",
            required_authority="interpret",
            context_profile="research",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=96_000, max_output_chars=48_000),
        ),
        CapabilitySpec(
            id="coding.software_engineering",
            description="Bounded software implementation and code maintenance work.",
            executor_kind="model",
            required_authority="modify_internal",
            context_profile="execute",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=96_000, max_output_chars=64_000),
        ),
        CapabilitySpec(
            id="documents.multimodal",
            description="Interpret multimodal document content when deterministic extraction is insufficient.",
            executor_kind="model",
            required_authority="interpret",
            context_profile="research",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=2, max_context_chars=128_000, max_output_chars=32_000),
        ),
    )
    for spec in specs:
        registry.register(spec)
