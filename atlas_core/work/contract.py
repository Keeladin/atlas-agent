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
    ContextPolicy,
    DataClassification,
    ExecutionBudget,
    ExecutorKind,
    HybridWeights,
    PrivacyRoute,
    RetryPolicy,
)
from atlas_core.capabilities.definition import CapabilityDefinition, lookup, require
from atlas_core.runtime_types import RuntimeBudget
from .store_common import _new_id, _payload_hash
from atlas_core.tools import ToolGateway

from .inventory import DeploymentInventory
from .work import WorkError

_DETERMINISTIC_KINDS = frozenset({"deterministic", "tool", "composite"})
_HANDLERLESS_KINDS = frozenset({"model", "human"})


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
    side_effects: tuple[str, ...] = ()
    idempotent: bool = True
    parallel_safe: bool = False
    privacy: PrivacyRoute | None = None
    data_classification: DataClassification | None = None
    context_policy: ContextPolicy | None = None
    context_profile: str | None = None
    budget: ExecutionBudget | None = None
    retry_policy: RetryPolicy | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
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

    def capability(self, capability_id: str) -> ContractCapability:
        for item in self.capabilities:
            if item.capability_id == capability_id:
                return item
        raise WorkError(f"Capability {capability_id!r} is not in this contract")

    def as_payload(self) -> dict[str, Any]:
        """Canonical dict for hashing. Excludes ``contract_id`` and ``sha256``."""

        return {
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


def compile_contract(
    *,
    work_id: str,
    brief: TaskBrief,
    authority_scope: str,
    inventory: DeploymentInventory,
    tools: ToolGateway | None = None,
    work_budget: RuntimeBudget | None = None,
    compiled_at: str | None = None,
    contract_id: str | None = None,
) -> WorkContract:
    """Compile a sealed WorkContract from a brief. Does not persist.

    Iterates ``brief.capabilities`` only. Does not scan inventory, gateway
    manifests, bindings indexes, registries, planners, or MCP.
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
    seen: set[str] = set()
    allowed: list[str] = []
    confirmations: list[str] = []
    for capability_id in brief.capabilities:
        if capability_id in seen:
            continue
        seen.add(capability_id)
        definition = lookup(capability_id)
        if definition is None:
            raise WorkError(f"Unknown capability: {capability_id}")
        if not authority_allows(granted, definition.required_authority):
            raise WorkError(
                f"authority_scope {granted!r} does not satisfy {definition.id} "
                f"required_authority {definition.required_authority!r}"
            )
        pin = _compile_pin(definition, inventory, tools)
        slices.append(pin)
        if pin.armed:
            for tool_ref in pin.tools:
                if tool_ref not in allowed:
                    allowed.append(tool_ref)
        if definition.confirmation == "required":
            confirmations.append(capability_id)

    draft = WorkContract(
        work_id=work_id,
        contract_id="pending",
        compiled_at=compiled_at or _compiled_at_now(),
        objective=brief.objective,
        success_criteria=(brief.expected_effect,),
        constraints=brief.constraints,
        authority_scope=granted,
        capabilities=tuple(slices),
        allowed_tools=tuple(allowed),
        confirmation_requirements=tuple(confirmations),
        work_budget=work_budget or RuntimeBudget(),
        sha256="pending",
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
) -> ContractCapability:
    profile = inventory.get(definition.id)
    unarmed = ContractCapability(
        capability_id=definition.id,
        definition=definition,
        armed=False,
        confirmation=definition.confirmation,
        required_authority=definition.required_authority,
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
    except KeyError:
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
        side_effects=profile.side_effects,
        idempotent=profile.idempotent,
        parallel_safe=profile.parallel_safe,
        privacy=profile.privacy,
        data_classification=profile.data_classification,
        context_policy=profile.context_policy,
        context_profile=profile.context_profile,
        budget=profile.budget,
        retry_policy=profile.retry_policy,
    )


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
    if str(payload.get("work_id") or "") != work_id:
        raise WorkError("Work contract work_id does not match the store row")
    if str(payload.get("compiled_at") or "") != compiled_at:
        raise WorkError("Work contract compiled_at does not match the store row")

    pins: list[ContractCapability] = []
    seen: set[str] = set()
    for item in payload.get("capabilities") or ():
        if not isinstance(item, dict):
            raise WorkError("Work contract capability slice is not an object")
        pin = _pin_from_payload(item)
        if pin.capability_id in seen:
            raise WorkError(
                f"Work contract repeats capability {pin.capability_id!r}"
            )
        seen.add(pin.capability_id)
        pins.append(pin)

    budget_payload = payload.get("work_budget")
    if not isinstance(budget_payload, dict):
        raise WorkError("Work contract work_budget is not an object")
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
    armed = bool(item.get("armed"))
    if not armed:
        return ContractCapability(
            capability_id=capability_id,
            definition=definition,
            armed=False,
            confirmation=definition.confirmation,
            required_authority=definition.required_authority,
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
    )


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
