from __future__ import annotations

from dataclasses import dataclass, field

from atlas_core.capabilities.registry import CapabilityHandler
from atlas_core.tools import ToolDescriptor, ToolGateway, ToolHandler

from .contract import ContractCapability, WorkContract
from .inventory import DeploymentInventory

_DETERMINISTIC_KINDS = frozenset({"deterministic", "tool", "composite"})


@dataclass(frozen=True)
class ResolvedCapability:
    pin: ContractCapability
    handler: CapabilityHandler | None
    tool_specs: tuple[ToolDescriptor, ...]
    tool_handlers: dict[str, ToolHandler] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedWork:
    contract: WorkContract
    capabilities: dict[int, ResolvedCapability]


@dataclass(frozen=True)
class ResolveMismatch:
    capability_id: str
    reason: str
    contract_capability_ordinal: int | None = None


@dataclass(frozen=True)
class ResolveReport:
    resolved: ResolvedWork
    unarmed: tuple[str, ...]
    mismatches: tuple[ResolveMismatch, ...]


class ImplementationResolver:
    """Bind callables to contract pins. Never widens the contract."""

    def resolve(
        self,
        contract: WorkContract,
        inventory: DeploymentInventory,
        tool_inventory: ToolGateway | None = None,
    ) -> ResolveReport:
        bound: dict[int, ResolvedCapability] = {}
        unarmed: list[str] = []
        mismatches: list[ResolveMismatch] = []
        for pin in contract.capabilities:
            if not pin.armed:
                unarmed.append(pin.capability_id)
                continue
            mismatch = _match_pin(pin, inventory, tool_inventory)
            if mismatch is not None:
                mismatches.append(mismatch)
                continue
            bound[pin.contract_capability_ordinal or 0] = _bind_pin(pin, inventory, tool_inventory)
        return ResolveReport(
            resolved=ResolvedWork(contract, bound),
            unarmed=tuple(unarmed),
            mismatches=tuple(mismatches),
        )


def _match_pin(
    pin: ContractCapability,
    inventory: DeploymentInventory,
    tool_inventory: ToolGateway | None,
) -> ResolveMismatch | None:
    if not pin.profile_version:
        return ResolveMismatch(pin.capability_id, "version_missing", pin.contract_capability_ordinal)
    profile = inventory.get(pin.capability_id, pin.profile_version)
    if profile is None:
        return ResolveMismatch(pin.capability_id, "version_missing", pin.contract_capability_ordinal)
    if not _execution_snapshot_matches(pin, profile):
        return ResolveMismatch(pin.capability_id, "profile_mismatch", pin.contract_capability_ordinal)
    if pin.executor_kind in _DETERMINISTIC_KINDS:
        if inventory.handler(pin.capability_id, pin.profile_version) is None:
            return ResolveMismatch(pin.capability_id, "handler_missing", pin.contract_capability_ordinal)
    for reference in pin.tools:
        if tool_inventory is None:
            return ResolveMismatch(pin.capability_id, "tool_missing", pin.contract_capability_ordinal)
        try:
            _get_pinned_tool(tool_inventory, reference)
        except KeyError:
            return ResolveMismatch(pin.capability_id, "tool_missing", pin.contract_capability_ordinal)
    return None


def _execution_snapshot_matches(pin: ContractCapability, profile) -> bool:
    """The live id@version document must equal the frozen pin.

    Handler callables are not part of the document. In-process they cannot
    change because DeploymentInventory forbids replacing a version.
    """

    return (
        profile.version == pin.profile_version
        and profile.executor_kind == pin.executor_kind
        and profile.implementation == pin.binding
        and _profile_tools_match_pin(profile.tools, pin.tools)
        and profile.verifier_id == pin.verifier_id
        and profile.verification_required == pin.verification_required
        and (profile.input_schema or {}) == (pin.input_schema or {})
        and (profile.output_schema or {}) == (pin.output_schema or {})
        and profile.output_kind == pin.output_kind
        and profile.requires_artifact_kinds == pin.requires_artifact_kinds
        and profile.eligible_providers == pin.eligible_providers
        and profile.side_effects == pin.side_effects
        and profile.idempotent == pin.idempotent
        and profile.parallel_safe == pin.parallel_safe
        and profile.privacy == pin.privacy
        and profile.data_classification == pin.data_classification
        and profile.context_policy == pin.context_policy
        and profile.context_profile == pin.context_profile
        and profile.budget == pin.budget
        and profile.retry_policy == pin.retry_policy
    )


def _profile_tools_match_pin(profile_tools: tuple[str, ...], pin_tools: tuple[str, ...]) -> bool:
    if any("@" in str(item) for item in profile_tools):
        return tuple(str(item) for item in profile_tools) == tuple(pin_tools)
    return _tool_id_set(profile_tools) == _tool_id_set(pin_tools)


def _bind_pin(
    pin: ContractCapability,
    inventory: DeploymentInventory,
    tool_inventory: ToolGateway | None,
) -> ResolvedCapability:
    handler = inventory.handler(pin.capability_id, pin.profile_version)
    specs: list[ToolDescriptor] = []
    handlers: dict[str, ToolHandler] = {}
    for reference in pin.tools:
        assert tool_inventory is not None
        spec, tool_handler = _get_pinned_tool(tool_inventory, reference)
        specs.append(spec)
        handlers[f"{spec.id}@{spec.version}"] = tool_handler
    return ResolvedCapability(pin, handler, tuple(specs), handlers)


def _get_pinned_tool(tools: ToolGateway, reference: str) -> tuple[ToolDescriptor, ToolHandler]:
    raw = str(reference or "").strip()
    if "@" not in raw:
        raise KeyError(f"pinned tool is not an exact ref: {reference}")
    tool_id, version = raw.rsplit("@", 1)
    return tools.get(tool_id, version)


def _tool_id_set(refs: tuple[str, ...]) -> set[str]:
    names: set[str] = set()
    for raw in refs:
        item = str(raw)
        names.add(item.rsplit("@", 1)[0] if "@" in item else item)
    return names
