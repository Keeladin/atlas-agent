from __future__ import annotations

from typing import Any

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, ScopeResolution

from .store import ArtifactStore


class ArtifactRuntime:
    """Read-only artifact surface.

    The plane owns no world-mutating capability: every change to reality stays a
    files or provider invocation under its own scope. Registration happens inside
    the executors that create bytes, never as a model-callable action.
    """

    def __init__(self, store: ArtifactStore, registry: CapabilityRegistry) -> None:
        self.store = store
        self.registry = registry
        self._register()

    def _register(self) -> None:
        list_schema = {"type": "object", "properties": {
            "name_like": {"type": "string"},
            "byte_sha256": {"type": "string"},
            "state": {"type": "string", "enum": ["present", "stale", "missing"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        }, "additionalProperties": False}
        get_schema = {"type": "object", "required": ["artifact_id"], "properties": {
            "artifact_id": {"type": "string", "minLength": 1},
        }, "additionalProperties": False}

        self.registry.register(CapabilityRegistration(
            CapabilityDefinition("artifacts.list", "List durable Atlas artifacts by name, content hash or representation state.", "search", "none", list_schema, source="artifacts", tags=("artifacts", "files", "provenance")),
            lambda p: ScopeResolution("atlas/artifacts", dict(p), "List durable artifacts"),
            self._list_execute,
            metadata={"scope_hint": "atlas/artifacts", "requires_owner_context": True},
        ), replace=True)
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition("artifacts.get", "Inspect one artifact's identity, provenance and governed representations.", "inspect", "none", get_schema, source="artifacts", tags=("artifacts", "files", "provenance")),
            lambda p: ScopeResolution("atlas/artifacts", dict(p), f"Inspect artifact {p['artifact_id']}"),
            self._get_execute,
            metadata={"scope_hint": "atlas/artifacts", "requires_owner_context": True},
        ), replace=True)

    @staticmethod
    def _owner(payload: dict[str, Any]) -> str:
        payload.pop("__invocation_surface", None)
        owner = str(payload.pop("__owner_principal_id", "") or "")
        if not owner:
            raise ValueError("owner principal unavailable")
        return owner

    def _list_execute(self, payload: dict[str, Any]) -> ActionResult:
        owner = self._owner(payload)
        rows = self.store.list(
            owner, name_like=payload.get("name_like"), byte_sha256=payload.get("byte_sha256"),
            state=payload.get("state"), limit=int(payload.get("limit") or 100),
        )
        return ActionResult(True, list(rows), {"ok": True, "operation": "search", "count": len(rows)})

    def _get_execute(self, payload: dict[str, Any]) -> ActionResult:
        owner = self._owner(payload)
        try:
            item = self.store.get(payload["artifact_id"])
        except KeyError:
            return ActionResult(False, {}, {"ok": False, "operation": "inspect"},
                                error_code="artifact_unknown", error="unknown artifact")
        if item["principal_id"] != owner:
            return ActionResult(False, {}, {"ok": False, "operation": "inspect"},
                                error_code="artifact_unknown", error="unknown artifact")
        return ActionResult(True, item, {"ok": True, "operation": "inspect", "artifact_id": item["artifact_id"]})
