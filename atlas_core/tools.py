from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from atlas_core.authority import require_authority
from atlas_core.schema_validation import SchemaValidationError, validate_json
from atlas_core.capabilities import (
    CapabilityOutcome,
    CapabilityRequest,
    CapabilitySpec,
    ExecutionBudget,
)


_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class ToolOrigin:
    type: Literal["internal", "mcp", "api", "cli"] = "internal"
    server: str | None = None
    tool_name: str | None = None
    transport: Literal["stdio", "sse", "streamable-http"] | None = None
    internal_handler: str | None = None


@dataclass(frozen=True)
class ToolConstraints:
    roots: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    max_result_bytes: int | None = None
    timeout_sec: int | None = None
    sandbox: bool = False
    read_only: bool = False

    def __post_init__(self) -> None:
        if self.max_result_bytes is not None and self.max_result_bytes < 0:
            raise ValueError("max_result_bytes must be >= 0")
        if self.timeout_sec is not None and self.timeout_sec < 1:
            raise ValueError("timeout_sec must be >= 1")


@dataclass(frozen=True)
class ToolAuth:
    type: Literal["none", "platform_secret", "oauth", "env"] = "none"
    secret_ref: str | None = None
    scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.type in {"platform_secret", "oauth", "env"} and not self.secret_ref:
            raise ValueError(f"{self.type} auth requires secret_ref")


@dataclass(frozen=True)
class ToolDescriptor:
    """Versioned declarative description of one bounded tool surface."""

    id: str
    description: str
    required_authority: str = "read"
    input_schema: dict[str, Any] = field(default_factory=dict)
    side_effects: tuple[str, ...] = ()
    idempotent: bool = True
    verifier_id: str = "core.nonempty"
    version: str = "1.0.0"
    name: str | None = None
    output_schema: dict[str, Any] = field(default_factory=dict)
    origin: ToolOrigin = field(default_factory=ToolOrigin)
    permissions: tuple[str, ...] = ()
    constraints: ToolConstraints = field(default_factory=ToolConstraints)
    auth: ToolAuth = field(default_factory=ToolAuth)
    side_effect_class: Literal["none", "reversible", "irreversible"] | None = None
    privacy_level: Literal["public", "internal", "sensitive"] = "internal"
    tags: tuple[str, ...] = ()
    deprecated: bool = False
    replaced_by: str | None = None
    cost_hint: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Tool id must not be empty")
        if not self.description.strip():
            raise ValueError("Tool description must not be empty")
        if not _SEMVER.match(self.version):
            raise ValueError("Tool version must be SemVer (major.minor.patch)")
        require_authority(self.required_authority, "read")
        valid_permissions = {
            "read", "write", "search", "execute", "create", "update",
            "delete", "list", "admin",
        }
        unknown = set(self.permissions) - valid_permissions
        if unknown:
            raise ValueError(f"Unknown tool permissions: {sorted(unknown)}")
        if self.constraints.read_only and any(
            permission in {"write", "execute", "create", "update", "delete", "admin"}
            for permission in self.permissions
        ):
            raise ValueError("read_only tool cannot declare mutating permissions")
        if self.side_effect_class is not None and self.side_effect_class not in {
            "none", "reversible", "irreversible"
        }:
            raise ValueError("Unsupported side_effect_class")
        if self.deprecated and self.replaced_by is not None and not self.replaced_by.strip():
            raise ValueError("replaced_by must not be blank")

    @property
    def display_name(self) -> str:
        return self.name or self.id

    @property
    def effective_side_effect_class(self) -> str:
        if self.side_effect_class is not None:
            return self.side_effect_class
        if not self.side_effects:
            return "none"
        return "reversible" if self.idempotent else "irreversible"

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def as_context_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "ref": self.ref,
            "name": self.display_name,
            "description": self.description,
            "input_schema": self.input_schema,
            "permissions": list(self.permissions),
            "constraints": {
                "roots": list(self.constraints.roots),
                "allowed_commands": list(self.constraints.allowed_commands),
                "max_result_bytes": self.constraints.max_result_bytes,
                "timeout_sec": self.constraints.timeout_sec,
                "sandbox": self.constraints.sandbox,
                "read_only": self.constraints.read_only,
            },
            "side_effects": list(self.side_effects),
            "side_effect_class": self.effective_side_effect_class,
            "privacy_level": self.privacy_level,
        }


# Backwards-compatible name used by the first Atlas 2.0 runtime commit.
ToolSpec = ToolDescriptor


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: Any = None
    receipt: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[[dict[str, Any]], ToolResult]


def _version_key(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


class ToolGateway:
    """Normalized boundary for native tools, APIs and MCP adapters.

    Tool definitions are versioned and declarative; handlers remain injected
    implementation details. Credentials never live in ToolDescriptor instances.
    """

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], tuple[ToolDescriptor, ToolHandler]] = {}

    def register(
        self,
        spec: ToolDescriptor,
        handler: ToolHandler,
        *,
        replace: bool = False,
    ) -> None:
        key = (spec.id, spec.version)
        if key in self._tools and not replace:
            raise ValueError(f"Tool already registered: {spec.id}@{spec.version}")
        self._tools[key] = (spec, handler)

    def get(self, tool_id: str, version: str | None = None) -> tuple[ToolDescriptor, ToolHandler]:
        if version is not None:
            try:
                return self._tools[(tool_id, version)]
            except KeyError as exc:
                raise KeyError(f"Unknown tool: {tool_id}@{version}") from exc
        candidates = [
            item for (candidate_id, _), item in self._tools.items()
            if candidate_id == tool_id and not item[0].deprecated
        ]
        if not candidates:
            raise KeyError(f"Unknown tool: {tool_id}")
        return max(candidates, key=lambda item: _version_key(item[0].version))

    def descriptors(self, tool_ids: Iterable[str]) -> tuple[ToolDescriptor, ...]:
        result: list[ToolDescriptor] = []
        for reference in tool_ids:
            if "@" in reference:
                tool_id, version = reference.rsplit("@", 1)
                spec, _ = self.get(tool_id, version)
            else:
                spec, _ = self.get(reference)
            result.append(spec)
        return tuple(result)

    def manifest(self, *, include_all_versions: bool = False) -> list[dict[str, Any]]:
        if include_all_versions:
            specs = [spec for spec, _ in self._tools.values()]
        else:
            ids = sorted({tool_id for tool_id, _ in self._tools})
            specs = [self.get(tool_id)[0] for tool_id in ids]
        specs.sort(key=lambda spec: (spec.id, _version_key(spec.version)))
        return [spec.as_context_dict() for spec in specs]

    def invoke(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        authority_scope: str,
        version: str | None = None,
    ) -> ToolResult:
        spec, handler = self.get(tool_id, version)
        require_authority(authority_scope, spec.required_authority)
        try:
            validate_json(arguments, spec.input_schema, path="$.tool_input")
        except SchemaValidationError as exc:
            return ToolResult(False, error=f"tool input schema validation failed: {exc}", receipt={"ok": False})
        constraint_error = self._constraint_error(spec, arguments)
        if constraint_error:
            return ToolResult(False, error=constraint_error, receipt={"ok": False})
        try:
            result = handler(dict(arguments))
        except Exception as exc:
            return ToolResult(False, error=str(exc), receipt={"ok": False})
        if result.ok and spec.output_schema:
            try:
                validate_json(result.output, spec.output_schema, path="$.tool_output")
            except SchemaValidationError as exc:
                return ToolResult(
                    False,
                    output=result.output,
                    receipt=result.receipt or {"ok": False},
                    error=f"tool output schema validation failed: {exc}",
                    metrics=result.metrics,
                )
        if result.ok and spec.side_effects and not result.receipt:
            return ToolResult(
                False,
                output=result.output,
                error="side-effecting tool returned no receipt",
                receipt={"ok": False},
            )
        if result.ok and spec.constraints.max_result_bytes is not None:
            encoded = json.dumps(result.output, ensure_ascii=False, default=str).encode("utf-8")
            if len(encoded) > spec.constraints.max_result_bytes:
                return ToolResult(
                    False,
                    error="tool result exceeds descriptor max_result_bytes",
                    receipt={"ok": False},
                )
        return result

    def capability(
        self,
        tool_id: str,
        *,
        version: str | None = None,
        output_kind: str = "tool_result",
        budget: ExecutionBudget | None = None,
    ) -> tuple[CapabilitySpec, Callable[[CapabilityRequest], CapabilityOutcome]]:
        spec, _handler = self.get(tool_id, version)
        capability = CapabilitySpec(
            id=tool_id,
            version=spec.version,
            name=spec.display_name,
            description=spec.description,
            objective=f"Invoke the bounded tool {spec.display_name} under its descriptor constraints.",
            executor_kind="tool",
            required_authority=spec.required_authority,
            input_schema=spec.input_schema,
            output_schema=spec.output_schema,
            output_kind=output_kind,
            allowed_tools=(spec.ref,),
            side_effects=spec.side_effects,
            verifier_id=("core.receipt" if spec.side_effects else spec.verifier_id),
            verification_required=True,
            idempotent=spec.idempotent,
            data_classification=spec.privacy_level,
            budget=budget or ExecutionBudget(),
            tags=spec.tags,
        )

        def capability_handler(request: CapabilityRequest) -> CapabilityOutcome:
            arguments: dict[str, Any] = {}
            candidate_ids = request.direct_input_artifact_ids or request.input_artifact_ids
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
            result = self.invoke(
                tool_id,
                arguments,
                authority_scope=spec.required_authority,
                version=spec.version,
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

    @staticmethod
    def _constraint_error(spec: ToolDescriptor, arguments: dict[str, Any]) -> str | None:
        constraints = spec.constraints
        if constraints.allowed_commands:
            command_value = arguments.get("command")
            if command_value is not None:
                try:
                    parts = shlex.split(str(command_value))
                except ValueError:
                    return "tool command could not be parsed"
                if not parts or Path(parts[0]).name not in set(constraints.allowed_commands):
                    return "tool command is outside descriptor allowlist"
        if constraints.roots:
            concrete_roots = [
                Path(os.path.expanduser(root)).resolve()
                for root in constraints.roots
                if "${" not in root
            ]
            if concrete_roots:
                for key in ("path", "file_path", "directory", "root"):
                    if key not in arguments:
                        continue
                    candidate = Path(os.path.expanduser(str(arguments[key]))).resolve()
                    if not any(candidate == root or root in candidate.parents for root in concrete_roots):
                        return f"tool argument {key} is outside descriptor roots"
        return None


class MCPClientProtocol:
    """Transport-neutral surface expected from an MCP client adapter.

    Streamable HTTP is implemented by ``atlas_core.mcp_http.StreamableHTTPMCPClient``.
    """

    def list_tools(self) -> list[dict[str, Any]]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class MCPToolBridge:
    """Expose MCP through ToolGateway without making MCP Atlas's ontology."""

    def __init__(self, client: MCPClientProtocol) -> None:
        self.client = client

    def register_discovered(
        self,
        gateway: ToolGateway,
        *,
        prefix: str = "mcp",
        required_authority: str = "read",
        server_name: str = "mcp",
        transport: Literal["stdio", "sse", "streamable-http"] = "stdio",
    ) -> tuple[str, ...]:
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
                return ToolResult(
                    not is_error,
                    output=raw,
                    receipt={"ok": not is_error, "mcp_tool": _name},
                    error=("MCP tool reported an error" if is_error else None),
                )

            gateway.register(
                ToolDescriptor(
                    tool_id,
                    description,
                    required_authority=required_authority,
                    input_schema=schema,
                    origin=ToolOrigin(
                        type="mcp",
                        server=server_name,
                        tool_name=name,
                        transport=transport,
                    ),
                    tags=("mcp",),
                ),
                handler,
            )
            registered.append(tool_id)
        return tuple(registered)
