from __future__ import annotations

from .definition import require
from .execution import CapabilityExecutionProfile
from .contracts import ExecutionBudget


def register_intelligence_capabilities(
    inventory,
    *,
    eligible_providers: tuple[str, ...] = (),
    include_multimodal: bool = True,
) -> None:
    """Register model-backed intelligence profiles for this deployment.

    ``eligible_providers`` is the host allowlist of provider identities
    for generic text capabilities. Empty remains fail-closed at execute.
    Competence scores are not eligibility.

    ``documents.multimodal`` is not a generic text capability. Host
    auto-registration must pass ``include_multimodal=False`` unless a
    later design can prove multimodal support from canonical provider
    metadata. Overlay scores and provider presence are not that proof.
    """

    keys = tuple(str(key) for key in eligible_providers if str(key).strip())
    profiles = [
        CapabilityExecutionProfile(
            capability_id="planning.general",
            executor_kind="model",
            model_outcome_policy="deliverable_only",
            context_profile="plan",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            eligible_providers=keys,
            budget=ExecutionBudget(max_attempts=3, max_context_chars=48_000, max_output_chars=24_000),
        ),
        CapabilityExecutionProfile(
            capability_id="reasoning.general",
            executor_kind="model",
            model_outcome_policy="claim_bearing",
            context_profile="research",
            objective="General deliberate reasoning over bounded task evidence.",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            eligible_providers=keys,
            budget=ExecutionBudget(max_attempts=3, max_context_chars=64_000, max_output_chars=32_000),
        ),
        CapabilityExecutionProfile(
            capability_id="generation.compose",
            executor_kind="model",
            model_outcome_policy="deliverable_only",
            context_profile="compose",
            objective=(
                "Compose the user-requested artifact itself "
                "(story, letter, document, or other requested text). "
                "Do not analyze the request."
            ),
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            eligible_providers=keys,
            budget=ExecutionBudget(max_attempts=3, max_context_chars=48_000, max_output_chars=32_000),
        ),
        CapabilityExecutionProfile(
            capability_id="reasoning.deep_analysis",
            executor_kind="model",
            model_outcome_policy="claim_bearing",
            context_profile="research",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            eligible_providers=keys,
            budget=ExecutionBudget(max_attempts=3, max_context_chars=96_000, max_output_chars=48_000),
        ),
        CapabilityExecutionProfile(
            capability_id="coding.software_engineering",
            executor_kind="model",
            model_outcome_policy="deliverable_only",
            context_profile="execute",
            verifier_id="core.nonempty",
            privacy="cloud_allowed",
            eligible_providers=keys,
            budget=ExecutionBudget(max_attempts=3, max_context_chars=96_000, max_output_chars=64_000),
        ),
    ]
    if include_multimodal:
        profiles.append(
            CapabilityExecutionProfile(
                capability_id="documents.multimodal",
                executor_kind="model",
                model_outcome_policy="claim_bearing",
                context_profile="research",
                verifier_id="core.nonempty",
                privacy="cloud_allowed",
                eligible_providers=keys,
                budget=ExecutionBudget(max_attempts=2, max_context_chars=128_000, max_output_chars=32_000),
            )
        )
    for profile in profiles:
        require(profile.capability_id)
        inventory.register(profile)
