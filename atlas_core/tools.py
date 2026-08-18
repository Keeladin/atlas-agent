from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from atlas_core.authority import require_authority
from atlas_core.capabilities import CapabilityOutcome, CapabilityRequest, CapabilitySpec, ExecutionBudget


@dataclass(frozen=True)
class ToolSpec:
    id: str
    description: str
    required_authority: str = "read"
    input_schema: dict[str, Any] = field(default_factory=dict)
    side_effects: tuple[str, ...] = ()
    idempotent: bool = True
    verifier_id: str = "core.nonempty"


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: Any = None
    receipt: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[[dict[str, Any]], ToolResult]


class ToolGateway:
    """Normalized boundary for native tools, APIs and future MCP adapters."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler, *, replace: bool = False) -> None:
        if spec.id in self._tools and not replace:
            raise ValueError(f"Tool already registered: {spec.id}")
        self._tools[spec.id] = (spec, handler)

    def invoke(self, tool_id: str, arguments: dict[str, Any], *, authority_scope: str) -> ToolResult:
        try:
            spec, handler = self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {tool_id}") from exc
        require_authority(authority_scope, spec.required_authority)
        try:
            result = handler(dict(arguments))
        except Exception as exc:
            return ToolResult(False, error=str(exc), receipt={"ok": False})
        if result.ok and spec.side_effects and not result.receipt:
            return ToolResult(False, output=result.output, error="side-effecting tool returned no receipt", receipt={"ok": False})
        return result

    def capability(self, tool_id: str, *, output_kind: str = "tool_result", budget: ExecutionBudget | None = None) -> tuple[CapabilitySpec, Callable[[CapabilityRequest], CapabilityOutcome]]:
        spec, handler = self._tools[tool_id]
        capability = CapabilitySpec(
            id=tool_id,
            description=spec.description,
            executor_kind="tool",
            required_authority=spec.required_authority,
            input_schema=spec.input_schema,
            output_kind=output_kind,
            side_effects=spec.side_effects,
            verifier_id=("core.receipt" if spec.side_effects else spec.verifier_id),
            verification_required=True,
            idempotent=spec.idempotent,
            budget=budget or ExecutionBudget(),
        )

        def capability_handler(request: CapabilityRequest) -> CapabilityOutcome:
            arguments: dict[str, Any] = {}
            candidate_ids = (
                request.direct_input_artifact_ids
                or request.input_artifact_ids
            )
            artifacts_by_id = {
                str(item.get("id")): item
                for item in request.context.get("artifacts", [])
                if isinstance(item, dict)
            }
            for artifact_id in reversed(candidate_ids):
                item = artifacts_by_id.get(artifact_id)
                candidate = item.get("payload") if item else None
                if isinstance(candidate, dict):
                    arguments = dict(candidate)
                    break
            # Runtime already checked the task/approval boundary. Re-enter the
            # gateway at the capability's required authority so normalization and
            # side-effect receipt enforcement cannot be bypassed by wrapping a
            # tool as a capability.
            result = self.invoke(
                tool_id,
                arguments,
                authority_scope=spec.required_authority,
            )
            return CapabilityOutcome(
                "pass" if result.ok else "fail",
                output=result.output,
                output_kind=output_kind,
                receipt=result.receipt,
                metrics=result.metrics,
                error=result.error,
            )

        return capability, capability_handler


class MCPClientProtocol:
    """Transport-neutral surface expected from a future MCP client adapter."""

    def list_tools(self) -> list[dict[str, Any]]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class MCPToolBridge:
    """Expose an MCP client through Atlas ToolGateway without making MCP core architecture."""

    def __init__(self, client: MCPClientProtocol) -> None:
        self.client = client

    def register_discovered(self, gateway: ToolGateway, *, prefix: str = "mcp", required_authority: str = "read") -> tuple[str, ...]:
        registered: list[str] = []
        for item in self.client.list_tools():
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            tool_id = f"{prefix}.{name}"
            description = str(item.get("description") or f"MCP tool {name}")
            schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}

            def handler(arguments, *, _name=name):
                raw = self.client.call_tool(_name, arguments)
                is_error = bool(raw.get("isError")) if isinstance(raw, dict) else False
                return ToolResult(not is_error, output=raw, receipt={"ok": not is_error, "mcp_tool": _name}, error=("MCP tool reported an error" if is_error else None))

            gateway.register(ToolSpec(tool_id, description, required_authority=required_authority, input_schema=schema), handler)
            registered.append(tool_id)
        return tuple(registered)
