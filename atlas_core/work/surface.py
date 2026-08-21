from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from atlas_core.capabilities.registry import CapabilityHandler
from atlas_core.tools import ToolDescriptor, ToolGateway, ToolResult

from .resolve import ResolvedCapability


class SurfaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionSurface:
    """Per-step tool handle. Scope is the contracted pin, not ToolGateway."""

    work_id: str
    step_id: str
    authority_scope: str
    capability_id: str
    allowed_tools: frozenset[str]
    confirmation_required: bool
    eligible_providers: tuple[str, ...]
    handler: CapabilityHandler | None = None
    _kernel: ToolGateway | None = field(default=None, repr=False, compare=False)

    def descriptor(self, tool_id: str, *, version: str | None = None) -> ToolDescriptor:
        tool_name, pinned_version = self._exact_ref(tool_id, version)
        if self._kernel is None:
            raise SurfaceError("execution surface has no invoke kernel")
        spec, _handler = self._kernel.get(tool_name, pinned_version)
        return spec

    def invoke(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        version: str | None = None,
    ) -> ToolResult:
        try:
            tool_name, pinned_version = self._exact_ref(tool_id, version)
        except SurfaceError as exc:
            return ToolResult(False, error=str(exc), receipt={"ok": False})
        if self._kernel is None:
            return ToolResult(
                False,
                error="execution surface has no invoke kernel",
                receipt={"ok": False},
            )
        return self._kernel.invoke(
            tool_name,
            arguments,
            authority_scope=self.authority_scope,
            version=pinned_version,
        )

    def _exact_ref(self, tool_id: str, version: str | None) -> tuple[str, str]:
        raw = str(tool_id or "").strip()
        if not raw:
            raise SurfaceError("tool not on this work surface")
        asked_version = version
        name = raw
        if "@" in raw:
            name, embedded = raw.rsplit("@", 1)
            if asked_version is not None and asked_version != embedded:
                raise SurfaceError("tool not on this work surface")
            asked_version = embedded
        matches: list[tuple[str, str]] = []
        for reference in self.allowed_tools:
            if "@" not in reference:
                continue
            tool_name, pinned_version = reference.rsplit("@", 1)
            if tool_name != name:
                continue
            if asked_version is not None and pinned_version != asked_version:
                continue
            matches.append((tool_name, pinned_version))
        if len(matches) != 1:
            raise SurfaceError("tool not on this work surface")
        return matches[0]


def project_surface(
    resolved: ResolvedCapability,
    *,
    work_id: str,
    step_id: str,
    authority_scope: str,
    kernel: ToolGateway,
) -> ExecutionSurface:
    """Build a step surface from a resolved pin. Does not scan gateway inventory."""

    pin = resolved.pin
    return ExecutionSurface(
        work_id=work_id,
        step_id=step_id,
        authority_scope=authority_scope,
        capability_id=pin.capability_id,
        allowed_tools=frozenset(pin.tools),
        confirmation_required=pin.confirmation == "required",
        eligible_providers=pin.eligible_providers,
        handler=resolved.handler,
        _kernel=kernel,
    )
