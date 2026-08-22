from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from atlas_core.advanced.brief import TaskBrief
from atlas_core.authority import authority_allows, validate_authority
from atlas_core.capabilities.bindings import CapabilityBinding
from atlas_core.capabilities.contracts import (
    ConfirmationRequirement,
    CompletionGroundingPolicy,
    ContextPolicy,
    DataClassification,
    ExecutionBudget,
    ExecutorKind,
    HybridWeights,
    ModelOutcomePolicy,
    PrivacyRoute,
    RetryPolicy,
)
from atlas_core.capabilities.definition import CapabilityDefinition, lookup, require
from atlas_core.runtime_types import RuntimeBudget
from .store_common import _new_id, _payload_hash
from atlas_core.providers.registry import ProviderRegistryError
from atlas_core.tools import ToolGateway

from .inventory import DeploymentInventory
from .work import WorkError

_DETERMINISTIC_KINDS = frozenset({"deterministic", "tool", "composite"})
_HANDLERLESS_KINDS = frozenset({"model", "human"})
WORK_CONTRACT_PAYLOAD_VERSION = 1


@dataclass(frozen=True)
class PinnedProvider:
    """Frozen execution-semantic identity of one eligible provider key."""

    key: str
    model: str
    provider_kind: str
    local: bool
    max_context_chars: int
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    base_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "model": self.model,
            "provider_kind": self.provider_kind,
            "local": self.local,
            "max_context_chars": self.max_context_chars,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "base_url": self.base_url,
        }

    def matches(self, provider) -> bool:
        spec = provider.spec
        live_url = getattr(provider, "base_url", None)
        live_url = None if live_url is None else str(live_url)
        return (
            spec.key == self.key
            and spec.model == self.model
            and spec.provider_kind == self.provider_kind
            and spec.local == self.local
            and spec.max_context_chars == self.max_context_chars
            and spec.input_cost_per_million == self.input_cost_per_million
            and spec.output_cost_per_million == self.output_cost_per_million
            and live_url == self.base_url
        )


@dataclass(frozen=True)
class ContractCapability:
    """One brief capability's pinned slice inside a WorkContract."""

    capability_id: str
    definition: CapabilityDefinition
    armed: bool
    confirmation: ConfirmationRequirement
    required_authority: str
    profile_version: str | None = None
    executor_kind: ExecutorKind | None = None
    binding: CapabilityBinding | None = None
    tools: tuple[str, ...] = ()
    verifier_id: str | None = None
    verification_required: bool = False
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    output_kind: str = ""
    requires_artifact_kinds: tuple[str, ...] = ()
    eligible_providers: tuple[str, ...] = ()
    provider_snapshots: tuple[PinnedProvider, ...] = ()
    side_effects: tuple[str, ...] = ()
    idempotent: bool = True
    parallel_safe: bool = False
    privacy: PrivacyRoute | None = None
    data_classification: DataClassification | None = None
    context_policy: ContextPolicy | None = None
    context_profile: str | None = None
    budget: ExecutionBudget | None = None
    retry_policy: RetryPolicy | None = None
    model_outcome_policy: ModelOutcomePolicy = "deliverable_only"
    contract_capability_ordinal: int | None = None

    def __post_init__(self) -> None:
        if self.contract_capability_ordinal is not None and self.contract_capability_ordinal < 1:
            raise ValueError("Contract capability ordinal must be positive")
        if self.model_outcome_policy not in {
            "deliverable_only",
            "claim_bearing",
        }:
            raise ValueError("Unsupported model outcome policy")

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "capability_id": self.capability_id,
            "definition": self.definition.as_dict(),
            "armed": self.armed,
            "profile_version": self.profile_version,
            "executor_kind": self.executor_kind,
            "binding": None if self.binding is None else self.binding.as_dict(),
            "tools": list(self.tools),
            "verifier_id": self.verifier_id,
            "verification_required": self.verification_required,
            "input_schema": copy.deepcopy(self.input_schema or {}),
            "output_schema": copy.deepcopy(self.output_schema or {}),
            "output_kind": self.output_kind,
            "requires_artifact_kinds": list(self.requires_artifact_kinds),
            "eligible_providers": list(self.eligible_providers),
            "provider_snapshots": [item.as_dict() for item in self.provider_snapshots],
            "side_effects": list(self.side_effects),
            "idempotent": self.idempotent,
            "parallel_safe": self.parallel_safe,
            "privacy": self.privacy,
            "data_classification": self.data_classification,
            "context_policy": _context_policy_dict(self.context_policy),
            "context_profile": self.context_profile,
            "budget": _execution_budget_dict(self.budget),
            "retry_policy": _retry_policy_dict(self.retry_policy),
            "confirmation": self.confirmation,
            "required_authority": self.required_authority,
        }
        payload["model_outcome_policy"] = self.model_outcome_policy
        payload["contract_capability_ordinal"] = self.contract_capability_ordinal
        return payload


@dataclass(frozen=True)
class ContractCriterion:
    ordinal: int
    text: str
    satisfaction_policy: str = "deliverable"
    semantic_verification: str = "none"

    def __post_init__(self) -> None:
        if self.ordinal < 1 or not self.text.strip():
            raise ValueError("Contract criterion must have a positive ordinal and text")
        if self.satisfaction_policy not in {"deliverable", "evidence_grounded"}:
            raise ValueError("Unsupported criterion satisfaction policy")
        if self.semantic_verification not in {"none", "required"}:
            raise ValueError("Unsupported criterion semantic verification policy")

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "text": self.text,
            "satisfaction_policy": self.satisfaction_policy,
            "semantic_verification": self.semantic_verification,
        }


@dataclass(frozen=True)
class ContractCriterionBinding:
    criterion_ordinal: int
    contract_capability_ordinal: int

    def as_dict(self) -> dict[str, int]:
        return {
            "criterion_ordinal": self.criterion_ordinal,
            "contract_capability_ordinal": self.contract_capability_ordinal,
        }


@dataclass(frozen=True)
class WorkContract:
    """Immutable execution contract compiled at Work accept.

    ``contract_id`` and ``sha256`` are assigned after ``as_payload()`` is hashed
    and are not part of the hashed document.
    """

    work_id: str
    contract_id: str
    compiled_at: str
    objective: str
    success_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    authority_scope: str
    capabilities: tuple[ContractCapability, ...]
    allowed_tools: tuple[str, ...]
    confirmation_requirements: tuple[str, ...]
    work_budget: RuntimeBudget
    sha256: str
    completion_grounding_policy: CompletionGroundingPolicy = "none"
    criteria: tuple[ContractCriterion, ...] = ()
    criterion_bindings: tuple[ContractCriterionBinding, ...] = ()

    def __post_init__(self) -> None:
        if self.completion_grounding_policy not in {"none", "evidence_required"}:
            raise ValueError("Unsupported completion grounding policy")
        pin_ordinals = tuple(item.contract_capability_ordinal for item in self.capabilities)
        if any(item is None for item in pin_ordinals) or len(set(pin_ordinals)) != len(pin_ordinals):
            raise ValueError("Contract capability ordinals must be present and unique")
        criterion_ordinals = tuple(item.ordinal for item in self.criteria)
        if len(set(criterion_ordinals)) != len(criterion_ordinals):
            raise ValueError("Contract criterion ordinals must be unique")
        if any(
            item.criterion_ordinal not in criterion_ordinals
            or item.contract_capability_ordinal not in pin_ordinals
            for item in self.criterion_bindings
        ):
            raise ValueError("Contract criterion binding names an unknown criterion or capability")

    def contract_capability(self, ordinal: int) -> ContractCapability:
        for item in self.capabilities:
            if item.contract_capability_ordinal == ordinal:
                return item
        raise WorkError(f"Contract capability ordinal {ordinal!r} is not in this contract")

    def capability(self, capability_id: str) -> ContractCapability:
        matches = tuple(item for item in self.capabilities if item.capability_id == capability_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise WorkError(
                f"Capability {capability_id!r} has multiple contract occurrences; use its ordinal"
            )
        raise WorkError(f"Capability {capability_id!r} is not in this contract")

    def as_payload(self) -> dict[str, Any]:
        """Canonical dict for hashing. Excludes ``contract_id`` and ``sha256``."""

        payload = {
            "contract_payload_version": WORK_CONTRACT_PAYLOAD_VERSION,
            "work_id": self.work_id,
            "compiled_at": self.compiled_at,
            "objective": self.objective,
            "success_criteria": list(self.success_criteria),
            "constraints": list(self.constraints),
            "authority_scope": self.authority_scope,
            "capabilities": [item.as_dict() for item in self.capabilities],
            "allowed_tools": list(self.allowed_tools),
            "confirmation_requirements": list(self.confirmation_requirements),
            "work_budget": _runtime_budget_dict(self.work_budget),
        }
        payload["completion_grounding_policy"] = self.completion_grounding_policy
        payload["criteria"] = [item.as_dict() for item in self.criteria]
        payload["criterion_bindings"] = [item.as_dict() for item in self.criterion_bindings]
        return payload


def compile_contract(
    *,
    work_id: str,
    brief: TaskBrief,
    authority_scope: str,
    inventory: DeploymentInventory,
    tools: ToolGateway | None = None,
    providers=None,
    work_budget: RuntimeBudget | None = None,
    compiled_at: str | None = None,
    contract_id: str | None = None,
) -> WorkContract:
    """Compile a sealed WorkContract from a brief. Does not persist.

    Iterates ``brief.capabilities`` only. Named tools and eligible provider
    keys are looked up exactly. Does not scan inventory, gateway manifests,
    provider registries, bindings indexes, planners, or MCP.
    """

    work_id = str(work_id or "").strip()
    if not work_id:
        raise WorkError("work_id must not be empty")

    granted = validate_authority(authority_scope)
    if not authority_allows(granted, brief.required_authority):
        raise WorkError(
            "authority_scope "
            f"{granted!r} does not satisfy required_authority {brief.required_authority!r}"
        )

    slices: list[ContractCapability] = []
    allowed: list[str] = []
    confirmations: list[str] = []
    for pin_ordinal, capability_id in enumerate(brief.capabilities, start=1):
        definition = lookup(capability_id)
        if definition is None:
            raise WorkError(f"Unknown capability: {capability_id}")
        if not authority_allows(granted, definition.required_authority):
            raise WorkError(
                f"authority_scope {granted!r} does not satisfy {definition.id} "
                f"required_authority {definition.required_authority!r}"
            )
        pin = replace(
            _compile_pin(definition, inventory, tools, providers),
            contract_capability_ordinal=pin_ordinal,
        )
        slices.append(pin)
        if pin.armed:
            for tool_ref in pin.tools:
                if tool_ref not in allowed:
                    allowed.append(tool_ref)
        if definition.confirmation == "required":
            confirmations.append(capability_id)

    criteria = tuple(
        ContractCriterion(index, item.text, item.satisfaction_policy, item.semantic_verification)
        for index, item in enumerate(brief.criteria, start=1)
    ) or (ContractCriterion(
        ordinal=1, text=brief.expected_effect,
        satisfaction_policy="evidence_grounded" if brief.completion_grounding_policy == "evidence_required" else "deliverable",
        semantic_verification="required" if brief.completion_grounding_policy == "evidence_required" else "none",
    ),)
    criterion_bindings = tuple(
        ContractCriterionBinding(item.criterion_ordinal, item.capability_ordinal)
        for item in brief.criterion_bindings
    ) or tuple(
        ContractCriterionBinding(criterion.ordinal, pin.contract_capability_ordinal or 0)
        for criterion in criteria for pin in slices
    )
    draft = WorkContract(
        work_id=work_id,
        contract_id="pending",
        compiled_at=compiled_at or _compiled_at_now(),
        objective=brief.objective,
        success_criteria=tuple(item.text for item in criteria),
        constraints=brief.constraints,
        authority_scope=granted,
        capabilities=tuple(slices),
        allowed_tools=tuple(allowed),
        confirmation_requirements=tuple(confirmations),
        work_budget=work_budget or RuntimeBudget(),
        sha256="pending",
        completion_grounding_policy=brief.completion_grounding_policy,
        criteria=criteria,
        criterion_bindings=criterion_bindings,
    )
    _encoded, digest = _payload_hash(draft.as_payload())
    return replace(
        draft,
        contract_id=contract_id or _new_id("contract"),
        sha256=digest,
    )


def _compile_pin(
    definition: CapabilityDefinition,
    inventory: DeploymentInventory,
    tools: ToolGateway | None,
    providers=None,
) -> ContractCapability:
    profile = inventory.get(definition.id)
    unarmed = ContractCapability(
        capability_id=definition.id,
        definition=definition,
        armed=False,
        confirmation=definition.confirmation,
        required_authority=definition.required_authority,
        model_outcome_policy=(
            "deliverable_only"
            if profile is None
            else profile.model_outcome_policy
        ),
    )
    if profile is None:
        return unarmed

    kind = profile.executor_kind
    handler = inventory.handler(definition.id, profile.version)
    if kind in _DETERMINISTIC_KINDS and handler is None:
        return unarmed
    if kind not in _DETERMINISTIC_KINDS and kind not in _HANDLERLESS_KINDS:
        return unarmed

    try:
        pinned_tools = _pin_profile_tools(profile.tools, tools)
        provider_snapshots = _pin_eligible_providers(
            profile.eligible_providers, providers
        )
    except (KeyError, ProviderRegistryError):
        return unarmed

    return ContractCapability(
        capability_id=definition.id,
        definition=definition,
        armed=True,
        confirmation=definition.confirmation,
        required_authority=definition.required_authority,
        profile_version=profile.version,
        executor_kind=kind,
        binding=profile.implementation,
        tools=pinned_tools,
        verifier_id=profile.verifier_id,
        verification_required=profile.verification_required,
        input_schema=copy.deepcopy(profile.input_schema),
        output_schema=copy.deepcopy(profile.output_schema),
        output_kind=profile.output_kind,
        requires_artifact_kinds=profile.requires_artifact_kinds,
        eligible_providers=profile.eligible_providers,
        provider_snapshots=provider_snapshots,
        side_effects=profile.side_effects,
        idempotent=profile.idempotent,
        parallel_safe=profile.parallel_safe,
        privacy=profile.privacy,
        data_classification=profile.data_classification,
        context_policy=profile.context_policy,
        context_profile=profile.context_profile,
        budget=profile.budget,
        retry_policy=profile.retry_policy,
        model_outcome_policy=profile.model_outcome_policy,
    )


def _pin_eligible_providers(named, providers) -> tuple[PinnedProvider, ...]:
    keys = tuple(str(key) for key in named if str(key).strip())
    if not keys:
        return ()
    if providers is None:
        raise KeyError("no provider inventory")
    snapshots: list[PinnedProvider] = []
    for key in keys:
        provider = providers.get(key)
        spec = provider.spec
        if spec.key != key:
            raise KeyError("provider identity mismatch")
        base_url = getattr(provider, "base_url", None)
        snapshots.append(
            PinnedProvider(
                key=spec.key,
                model=spec.model,
                provider_kind=spec.provider_kind,
                local=spec.local,
                max_context_chars=spec.max_context_chars,
                input_cost_per_million=spec.input_cost_per_million,
                output_cost_per_million=spec.output_cost_per_million,
                base_url=None if base_url is None else str(base_url),
            )
        )
    return tuple(snapshots)


def _pin_profile_tools(
    named: tuple[str, ...],
    tools: ToolGateway | None,
) -> tuple[str, ...]:
    if not named:
        return ()
    if tools is None:
        raise KeyError("no tool inventory")
    pinned: list[str] = []
    for reference in named:
        spec, _handler = _get_named_tool(tools, reference)
        ref = f"{spec.id}@{spec.version}"
        if ref not in pinned:
            pinned.append(ref)
    return tuple(pinned)


def _get_named_tool(tools: ToolGateway, reference: str) -> tuple[Any, Any]:
    raw = str(reference or "").strip()
    if not raw:
        raise KeyError("empty tool reference")
    if "@" in raw:
        tool_id, version = raw.rsplit("@", 1)
        return tools.get(tool_id, version)
    return tools.get(raw)


def _compiled_at_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _runtime_budget_dict(budget: RuntimeBudget) -> dict[str, Any]:
    return {
        "max_executions": budget.max_executions,
        "max_cycles": budget.max_cycles,
        "max_model_calls": budget.max_model_calls,
        "max_parallel_workers": budget.max_parallel_workers,
        "max_cost_usd": budget.max_cost_usd,
    }


def _execution_budget_dict(budget: ExecutionBudget | None) -> dict[str, Any] | None:
    if budget is None:
        return None
    return {
        "max_attempts": budget.max_attempts,
        "timeout_seconds": budget.timeout_seconds,
        "max_context_chars": budget.max_context_chars,
        "max_output_chars": budget.max_output_chars,
        "max_cost_usd": budget.max_cost_usd,
        "max_tool_calls": budget.max_tool_calls,
    }


def _retry_policy_dict(policy: RetryPolicy | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    return {
        "retry_on": list(policy.retry_on),
        "stop_on": list(policy.stop_on),
    }


def work_contract_from_stored(
    *,
    work_id: str,
    contract_id: str,
    sha256: str,
    payload: dict[str, Any],
    compiled_at: str,
) -> WorkContract:
    """Rebuild a WorkContract from a verified store row.

    Catalog identity comes from ``require()``. This function does not construct
    ``CapabilityDefinition``.
    """

    _encoded, digest = _payload_hash(payload)
    if digest != sha256:
        raise WorkError("Work contract digest mismatch")
    if payload.get("contract_payload_version") != WORK_CONTRACT_PAYLOAD_VERSION:
        raise WorkError(
            "Unsupported Work contract payload version; reset obsolete development data"
        )
    required_fields = {
        "contract_payload_version", "work_id", "compiled_at", "objective",
        "success_criteria", "constraints", "authority_scope", "capabilities",
        "allowed_tools", "confirmation_requirements", "work_budget",
        "completion_grounding_policy", "criteria", "criterion_bindings",
    }
    missing = required_fields - set(payload)
    if missing:
        raise WorkError(
            "Current Work contract payload is missing required fields: "
            + ", ".join(sorted(missing))
        )
    if str(payload.get("work_id") or "") != work_id:
        raise WorkError("Work contract work_id does not match the store row")
    if str(payload.get("compiled_at") or "") != compiled_at:
        raise WorkError("Work contract compiled_at does not match the store row")

    pins: list[ContractCapability] = []
    capability_payloads = payload.get("capabilities") or ()
    for item in capability_payloads:
        if not isinstance(item, dict):
            raise WorkError("Work contract capability slice is not an object")
        required_capability_fields = {
            "capability_id", "definition", "armed", "profile_version",
            "executor_kind", "binding", "tools", "verifier_id",
            "verification_required", "input_schema", "output_schema", "output_kind",
            "requires_artifact_kinds", "eligible_providers", "provider_snapshots",
            "side_effects", "idempotent", "parallel_safe", "privacy",
            "data_classification", "context_policy", "context_profile", "budget",
            "retry_policy", "confirmation", "required_authority",
            "model_outcome_policy", "contract_capability_ordinal",
        }
        missing_capability_fields = required_capability_fields - set(item)
        if missing_capability_fields:
            raise WorkError(
                "Current Work contract capability is missing required fields: "
                + ", ".join(sorted(missing_capability_fields))
            )
        pin = replace(
            _pin_from_payload(item),
            contract_capability_ordinal=int(item["contract_capability_ordinal"]),
        )
        pins.append(pin)

    budget_payload = payload.get("work_budget")
    if not isinstance(budget_payload, dict):
        raise WorkError("Work contract work_budget is not an object")
    criteria = tuple(
        ContractCriterion(
            ordinal=int(item["ordinal"]),
            text=str(item["text"]),
            satisfaction_policy=str(item["satisfaction_policy"]),
            semantic_verification=str(item["semantic_verification"]),
        )
        for item in payload["criteria"]
    )
    bindings = tuple(
        ContractCriterionBinding(
            int(item["criterion_ordinal"]),
            int(item["contract_capability_ordinal"]),
        )
        for item in payload["criterion_bindings"]
    )
    return WorkContract(
        work_id=work_id,
        contract_id=contract_id,
        compiled_at=compiled_at,
        objective=str(payload.get("objective") or ""),
        success_criteria=tuple(
            str(item) for item in payload.get("success_criteria") or ()
        ),
        constraints=tuple(str(item) for item in payload.get("constraints") or ()),
        authority_scope=str(payload.get("authority_scope") or ""),
        capabilities=tuple(pins),
        allowed_tools=tuple(str(item) for item in payload.get("allowed_tools") or ()),
        confirmation_requirements=tuple(
            str(item) for item in payload.get("confirmation_requirements") or ()
        ),
        work_budget=RuntimeBudget(
            max_executions=int(budget_payload["max_executions"]),
            max_cycles=int(budget_payload["max_cycles"]),
            max_model_calls=int(budget_payload["max_model_calls"]),
            max_parallel_workers=int(budget_payload["max_parallel_workers"]),
            max_cost_usd=(
                None
                if budget_payload.get("max_cost_usd") is None
                else float(budget_payload["max_cost_usd"])
            ),
        ),
        sha256=sha256,
        completion_grounding_policy=str(
            payload["completion_grounding_policy"]
        ),
        criteria=criteria,
        criterion_bindings=bindings,
    )


def _pin_from_payload(item: dict[str, Any]) -> ContractCapability:
    capability_id = str(item.get("capability_id") or "").strip()
    if not capability_id:
        raise WorkError("Work contract capability id must not be empty")
    stored_definition = item.get("definition")
    if isinstance(stored_definition, dict):
        stored_id = str(stored_definition.get("id") or "").strip()
        if stored_id and stored_id != capability_id:
            raise WorkError(
                "Work contract capability id does not match stored definition id"
            )
    try:
        definition = require(capability_id)
    except ValueError as exc:
        raise WorkError(str(exc)) from exc
    if "model_outcome_policy" not in item:
        raise WorkError("Current Work contract capability is missing model outcome policy")
    armed = bool(item.get("armed"))
    if not armed:
        return ContractCapability(
            capability_id=capability_id,
            definition=definition,
            armed=False,
            confirmation=definition.confirmation,
            required_authority=definition.required_authority,
            model_outcome_policy=str(item["model_outcome_policy"]),
        )
    binding_payload = item.get("binding")
    binding = None
    if isinstance(binding_payload, dict):
        binding = CapabilityBinding(
            capability_id=str(binding_payload["capability_id"]),
            provider=str(binding_payload["provider"]),
            implementation=str(binding_payload["implementation"]),
            version=str(binding_payload.get("version") or "1"),
        )
    return ContractCapability(
        capability_id=capability_id,
        definition=definition,
        armed=True,
        confirmation=definition.confirmation,
        required_authority=definition.required_authority,
        profile_version=(
            None
            if item.get("profile_version") is None
            else str(item.get("profile_version"))
        ),
        executor_kind=item.get("executor_kind"),
        binding=binding,
        tools=tuple(str(tool) for tool in item.get("tools") or ()),
        verifier_id=(
            None if item.get("verifier_id") is None else str(item.get("verifier_id"))
        ),
        verification_required=bool(item.get("verification_required")),
        input_schema=copy.deepcopy(item.get("input_schema") or {}),
        output_schema=copy.deepcopy(item.get("output_schema") or {}),
        output_kind=str(item.get("output_kind") or ""),
        requires_artifact_kinds=tuple(
            str(kind) for kind in item.get("requires_artifact_kinds") or ()
        ),
        eligible_providers=tuple(
            str(provider) for provider in item.get("eligible_providers") or ()
        ),
        provider_snapshots=_pinned_providers_from_payload(
            item.get("provider_snapshots")
        ),
        side_effects=tuple(str(effect) for effect in item.get("side_effects") or ()),
        idempotent=bool(item.get("idempotent", True)),
        parallel_safe=bool(item.get("parallel_safe")),
        privacy=item.get("privacy"),
        data_classification=item.get("data_classification"),
        context_policy=_context_policy_from_dict(item.get("context_policy")),
        context_profile=(
            None
            if item.get("context_profile") is None
            else str(item.get("context_profile"))
        ),
        budget=_execution_budget_from_dict(item.get("budget")),
        retry_policy=_retry_policy_from_dict(item.get("retry_policy")),
        model_outcome_policy=str(item["model_outcome_policy"]),
    )


def _pinned_providers_from_payload(payload: Any) -> tuple[PinnedProvider, ...]:
    if not isinstance(payload, list):
        return ()
    snapshots: list[PinnedProvider] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        snapshots.append(
            PinnedProvider(
                key=str(item["key"]),
                model=str(item["model"]),
                provider_kind=str(item["provider_kind"]),
                local=bool(item["local"]),
                max_context_chars=int(item["max_context_chars"]),
                input_cost_per_million=(
                    None
                    if item.get("input_cost_per_million") is None
                    else float(item["input_cost_per_million"])
                ),
                output_cost_per_million=(
                    None
                    if item.get("output_cost_per_million") is None
                    else float(item["output_cost_per_million"])
                ),
                base_url=(
                    None if item.get("base_url") is None else str(item["base_url"])
                ),
            )
        )
    return tuple(snapshots)


def _execution_budget_from_dict(payload: Any) -> ExecutionBudget | None:
    if not isinstance(payload, dict):
        return None
    return ExecutionBudget(
        max_attempts=int(payload["max_attempts"]),
        timeout_seconds=(
            None
            if payload.get("timeout_seconds") is None
            else int(payload["timeout_seconds"])
        ),
        max_context_chars=int(payload["max_context_chars"]),
        max_output_chars=(
            None
            if payload.get("max_output_chars") is None
            else int(payload["max_output_chars"])
        ),
        max_cost_usd=(
            None if payload.get("max_cost_usd") is None else float(payload["max_cost_usd"])
        ),
        max_tool_calls=(
            None
            if payload.get("max_tool_calls") is None
            else int(payload["max_tool_calls"])
        ),
    )


def _retry_policy_from_dict(payload: Any) -> RetryPolicy | None:
    if not isinstance(payload, dict):
        return None
    return RetryPolicy(
        retry_on=tuple(str(item) for item in payload.get("retry_on") or ()),
        stop_on=tuple(str(item) for item in payload.get("stop_on") or ()),
    )


def _context_policy_from_dict(payload: Any) -> ContextPolicy | None:
    if not isinstance(payload, dict):
        return None
    weights_payload = payload.get("hybrid_weights") or {}
    weights = HybridWeights(
        semantic=float(weights_payload.get("semantic", 0.7)),
        recency=float(weights_payload.get("recency", 0.2)),
        importance=float(weights_payload.get("importance", 0.1)),
    )
    return ContextPolicy(
        max_tokens=int(payload["max_tokens"]),
        max_memory_items=int(payload["max_memory_items"]),
        max_artifact_items=int(payload["max_artifact_items"]),
        max_recent_steps=int(payload["max_recent_steps"]),
        min_relevance_score=float(payload["min_relevance_score"]),
        per_item_token_cap=int(payload["per_item_token_cap"]),
        allow_full_artifact=bool(payload.get("allow_full_artifact", True)),
        must_include=tuple(str(item) for item in payload.get("must_include") or ()),
        must_exclude=tuple(str(item) for item in payload.get("must_exclude") or ()),
        hybrid_weights=weights,
    )


def _context_policy_dict(policy: ContextPolicy | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    weights = policy.hybrid_weights
    return {
        "max_tokens": policy.max_tokens,
        "max_memory_items": policy.max_memory_items,
        "max_artifact_items": policy.max_artifact_items,
        "max_recent_steps": policy.max_recent_steps,
        "min_relevance_score": policy.min_relevance_score,
        "per_item_token_cap": policy.per_item_token_cap,
        "allow_full_artifact": policy.allow_full_artifact,
        "must_include": list(policy.must_include),
        "must_exclude": list(policy.must_exclude),
        "hybrid_weights": {
            "semantic": weights.semantic,
            "recency": weights.recency,
            "importance": weights.importance,
        },
    }
