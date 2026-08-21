from __future__ import annotations

from .definition import require
from .execution import CapabilityExecutionProfile
from .contracts import ExecutionBudget


def register_intelligence_capabilities(inventory) -> None:
    profiles = (
        CapabilityExecutionProfile(
            capability_id="planning.general",
            executor_kind="model",
            context_profile="plan",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=48_000, max_output_chars=24_000),
        ),
        CapabilityExecutionProfile(
            capability_id="reasoning.general",
            executor_kind="model",
            context_profile="research",
            objective="General deliberate reasoning over bounded task evidence.",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=64_000, max_output_chars=32_000),
        ),
        CapabilityExecutionProfile(
            capability_id="generation.compose",
            executor_kind="model",
            context_profile="compose",
            objective=(
                "Compose the user-requested artifact itself "
                "(story, letter, document, or other requested text). "
                "Do not analyze the request."
            ),
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=48_000, max_output_chars=32_000),
        ),
        CapabilityExecutionProfile(
            capability_id="reasoning.deep_analysis",
            executor_kind="model",
            context_profile="research",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=96_000, max_output_chars=48_000),
        ),
        CapabilityExecutionProfile(
            capability_id="coding.software_engineering",
            executor_kind="model",
            context_profile="execute",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=3, max_context_chars=96_000, max_output_chars=64_000),
        ),
        CapabilityExecutionProfile(
            capability_id="documents.multimodal",
            executor_kind="model",
            context_profile="research",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            budget=ExecutionBudget(max_attempts=2, max_context_chars=128_000, max_output_chars=32_000),
        ),
    )
    for profile in profiles:
        require(profile.capability_id)
        inventory.register(profile)
