from __future__ import annotations

from typing import Any

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, ScopeResolution

from .store import ArtifactStore
from .inspection import inspect_payload, metadata_only_inspection


class ArtifactRuntime:
    """Read-only artifact surface.

    The plane owns no world-mutating capability: every change to reality stays a
    files or provider invocation under its own scope. Registration happens inside
    the executors that create bytes, never as a model-callable action.
    """

    def __init__(self, store: ArtifactStore, registry: CapabilityRegistry, sources=None) -> None:
        self.store = store
        self.registry = registry
        self.sources = sources
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
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition("artifacts.inspect", "Build a bounded structural inspection of an artifact representation without deciding its semantic purpose.", "inspect", "none", get_schema, source="artifacts", tags=("artifacts", "inspection", "sources")),
            self._inspect_scope, self._inspect_execute,
            metadata={"scope_hint": "files", "requires_owner_context": True},
        ), replace=True)
        if self.sources is not None:
            diff_schema = {"type": "object", "required": ["root_id"], "properties": {
                "root_id": {"type": "string", "minLength": 1},
                "relative_path": {"type": "string"},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 10000},
            }, "additionalProperties": False}
            self.registry.register(CapabilityRegistration(
                CapabilityDefinition("artifacts.diff_source", "Detect new, changed and missing files in an enrolled source without interpreting their meaning.", "diff", "none", diff_schema, source="artifacts", tags=("artifacts", "sources", "monitoring")),
                self._diff_scope, self._diff_execute, metadata={"scope_hint": "files", "requires_owner_context": True},
            ), replace=True)

    def _inspect_scope(self, payload: dict[str, Any]) -> ScopeResolution:
        artifact = self.store.get(str(payload.get("artifact_id") or ""))
        facet = next((row for row in artifact.get("facets", []) if row.get("kind") == "local_file" and row.get("root_id") and row.get("relative_path")), None)
        if facet is None or self.sources is None:
            return ScopeResolution(f"atlas/artifacts/{artifact['artifact_id']}", dict(payload), f"Inspect artifact {artifact['display_name']}")
        root = self.sources.store.get(facet["root_id"])
        scope = f"files/{root.provider_namespace}/{root.root_id}/{facet['relative_path']}"
        return ScopeResolution(scope, dict(payload), f"Inspect artifact {artifact['display_name']}")

    def _inspect_execute(self, payload: dict[str, Any]) -> ActionResult:
        owner = self._owner(payload)
        try:
            artifact = self.store.get(payload["artifact_id"])
        except KeyError:
            return ActionResult(False, {}, {"ok": False, "operation": "inspect"}, error_code="artifact_unknown", error="unknown artifact")
        if artifact["principal_id"] != owner:
            return ActionResult(False, {}, {"ok": False, "operation": "inspect"}, error_code="artifact_unknown", error="unknown artifact")
        facet = next((row for row in artifact.get("facets", []) if row.get("kind") == "local_file" and row.get("root_id") and row.get("relative_path")), None)
        try:
            if facet is None or self.sources is None:
                inspection = metadata_only_inspection(artifact)
            else:
                root = self.sources.store.get(facet["root_id"])
                probe = self.sources.kernel.probe(root.provider_namespace, root.root_id, facet["relative_path"], configuration_revision=self.sources._revision(root))
                inspection = inspect_payload(facet["relative_path"], probe["raw"], complete=bool(probe["complete"]), observation=probe["observation"])
                self.sources._verify_facet(root, facet["relative_path"], probe["observation"])
            out = {"artifact_id": artifact["artifact_id"], "display_name": artifact["display_name"], "media_type": artifact.get("media_type"), "inspection": inspection}
            return ActionResult(True, out, {"ok": True, "operation": "inspect", "artifact_id": artifact["artifact_id"], "inspection_status": inspection["inspection_status"]})
        except Exception as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "inspect"}, error_code="artifact_inspection_failed", error=str(exc))


    def _diff_scope(self, payload: dict[str, Any]) -> ScopeResolution:
        root_id = str(payload.get("root_id") or "").strip()
        row = self.sources.store.get(root_id)
        relative = str(payload.get("relative_path") or ".")
        from atlas_core.sources import validate_relative_path
        relative = validate_relative_path(relative)
        clean = dict(payload); clean["root_id"] = row.root_id; clean["relative_path"] = relative
        scope = f"files/{row.provider_namespace}/{row.root_id}" if relative == "." else f"files/{row.provider_namespace}/{row.root_id}/{relative}"
        return ScopeResolution(scope, clean, f"Detect changes in source {row.display_name}")

    def _diff_execute(self, payload: dict[str, Any]) -> ActionResult:
        owner = str(payload.pop("__owner_principal_id", "") or ""); payload.pop("__invocation_surface", None)
        try:
            if not owner: raise ValueError("owner principal unavailable")
            result = self.sources.diff_source(payload["root_id"], payload.get("relative_path", "."), max_entries=int(payload.get("max_entries") or 1000), principal_id=owner)
            return ActionResult(True, result, {"ok": True, "operation": "diff", "root_id": payload["root_id"], "counts": result["counts"]})
        except Exception as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "diff"}, error_code="artifact_source_diff_failed", error=str(exc))

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
