from __future__ import annotations

from typing import Any

import magic

from atlas_core.actions import ActionResult
from atlas_core.capabilities import (
    CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, ScopeResolution,
)

from .inspection import inspect_payload

MANAGED_ROOT_ID = "atlas-managed-intake"
MANAGED_PROVIDER_NAMESPACE = "atlas-managed"

_FORMATS: dict[str, tuple[str, str]] = {
    "pdf": (".pdf", "application/pdf"),
    "png": (".png", "image/png"),
    "jpeg": (".jpg", "image/jpeg"),
    "gif": (".gif", "image/gif"),
    "webp": (".webp", "image/webp"),
    "docx": (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pptx": (".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "zip": (".zip", "application/zip"),
    "markdown": (".md", "text/markdown"),
    "html": (".html", "text/html"),
    "xml": (".xml", "application/xml"),
    "json": (".json", "application/json"),
    "csv": (".csv", "text/csv"),
    "text": (".txt", "text/plain"),
    "doc": (".doc", "application/msword"),
    "xls": (".xls", "application/vnd.ms-excel"),
    "ppt": (".ppt", "application/vnd.ms-powerpoint"),
    "docm": (".docm", "application/vnd.ms-word.document.macroenabled.12"),
    "xlsm": (".xlsm", "application/vnd.ms-excel.sheet.macroenabled.12"),
    "pptm": (".pptm", "application/vnd.ms-powerpoint.presentation.macroenabled.12"),
}


class ManagedIntakeRuntime:
    """Acquire external source bytes into Atlas custody before semantic work begins."""

    def __init__(self, artifacts, sources, registry: CapabilityRegistry) -> None:
        self.artifacts = artifacts
        self.sources = sources
        self.registry = registry
        self._register()

    def _register(self) -> None:
        schema = {
            "type": "object",
            "required": ["artifact_id"],
            "properties": {"artifact_id": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        }
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition(
                "artifacts.verify_format",
                "Verify an established source artifact's true MIME type from file signatures before managed custody.",
                "verify_format", "none", schema, source="artifacts",
                tags=("artifacts", "intake", "format", "libmagic"),
            ),
            self._scope, self._verify_execute, availability=_magic_available,
            metadata={"scope_hint": "files", "requires_owner_context": True},
        ), replace=True)
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition(
                "artifacts.acquire_managed",
                "Acquire an established source artifact into immutable Atlas-managed custody by content identity.",
                "acquire", "internal", schema, source="artifacts",
                tags=("artifacts", "intake", "content-identity", "managed"),
            ),
            self._scope, self._execute, availability=_magic_available,
            metadata={"scope_hint": "files", "requires_owner_context": True},
        ), replace=True)

    def _source(self, artifact_id: str) -> tuple[dict[str, Any], dict[str, Any], Any]:
        artifact = self.artifacts.get(artifact_id)
        if self.artifacts.managed_by_artifact(artifact_id) is not None:
            raise ValueError("artifact is already a managed content artifact")
        facet = next((row for row in artifact.get("facets", [])
                      if row.get("kind") == "local_file" and row.get("root_id") and row.get("relative_path")), None)
        if facet is None:
            raise ValueError("managed intake requires a governed local source representation")
        root = self.sources.store.get(facet["root_id"])
        if root.provider_namespace == MANAGED_PROVIDER_NAMESPACE:
            raise ValueError("managed intake cannot acquire from the managed-intake root")
        return artifact, facet, root

    def _scope(self, payload: dict[str, Any]) -> ScopeResolution:
        artifact, facet, root = self._source(str(payload.get("artifact_id") or ""))
        scope = f"files/{root.provider_namespace}/{root.root_id}/{facet['relative_path']}"
        return ScopeResolution(scope, dict(payload), f"Acquire {artifact['display_name']} into managed intake")


    def _verify(self, source_artifact: dict[str, Any], facet: dict[str, Any], source_root: Any) -> dict[str, Any]:
        probe = self.sources.kernel.probe(
            source_root.provider_namespace, source_root.root_id, facet["relative_path"],
            configuration_revision=self.sources._revision(source_root),
        )
        raw = probe["raw"]
        detected_mime = str(magic.from_buffer(raw or b"\x00", mime=True) or "application/octet-stream").lower()
        inspection = inspect_payload(
            facet["relative_path"], raw, complete=bool(probe["complete"]),
            observation=probe["observation"],
        )
        inspected_format = str(inspection.get("format") or "binary")
        fmt, extension, media_type = _verified_format(detected_mime, inspected_format, facet["relative_path"])
        original_extension = _safe_extension(facet["relative_path"])
        return {
            "artifact_id": source_artifact["artifact_id"], "detected_mime": detected_mime,
            "format": fmt, "canonical_extension": extension, "media_type": media_type,
            "original_extension": original_extension,
            "extension_mismatch": bool(original_extension and original_extension != extension),
            "probe_complete": bool(probe["complete"]), "observation": probe["observation"],
        }

    def _verify_execute(self, payload: dict[str, Any]) -> ActionResult:
        owner = str(payload.pop("__owner_principal_id", "") or "")
        payload.pop("__invocation_surface", None)
        try:
            artifact, facet, root = self._source(payload["artifact_id"])
            if not owner or artifact["principal_id"] != owner:
                raise KeyError(payload["artifact_id"])
            verified = self._verify(artifact, facet, root)
            return ActionResult(True, verified, {
                "ok": True, "operation": "verify_format", "artifact_id": artifact["artifact_id"],
                "detected_mime": verified["detected_mime"], "format": verified["format"],
                "extension_mismatch": verified["extension_mismatch"],
            })
        except Exception as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "verify_format"},
                                error_code="format_verification_failed", error=str(exc))

    def _execute(self, payload: dict[str, Any]) -> ActionResult:
        owner = str(payload.pop("__owner_principal_id", "") or "")
        payload.pop("__invocation_surface", None)
        try:
            source_artifact, facet, source_root = self._source(payload["artifact_id"])
            if not owner or source_artifact["principal_id"] != owner:
                raise KeyError(payload["artifact_id"])
            verified = self._verify(source_artifact, facet, source_root)
            fmt = verified["format"]
            extension = verified["canonical_extension"]
            media_type = verified["media_type"]
            acquired = self.sources.kernel.acquire_managed_copy(
                source_root.provider_namespace, source_root.root_id, facet["relative_path"],
                destination_namespace=MANAGED_PROVIDER_NAMESPACE, destination_root_id=MANAGED_ROOT_ID,
                canonical_extension=extension, configuration_revision=self.sources._revision(source_root),
                destination_revision=self.sources._revision(self.sources.store.get(MANAGED_ROOT_ID)),
            )
            digest = acquired["content_sha256"]
            existing = self.artifacts.managed_by_hash(digest)
            reused = existing is not None
            if existing is None:
                managed_artifact_id = self.artifacts.register(
                    principal_id=owner, display_name=source_artifact["display_name"],
                    occurrence_id="artifacts.acquire_managed", media_type=media_type,
                    provenance={
                        "parents": [source_artifact["artifact_id"]], "relation": "managed_copy",
                        "content_sha256": digest, "format": fmt, "detected_mime": verified["detected_mime"],
                        "canonical_extension": extension, "extension_mismatch": verified["extension_mismatch"],
                        "original_display_name": source_artifact["display_name"],
                        "original_root_id": facet["root_id"], "original_relative_path": facet["relative_path"],
                    },
                )
                self.artifacts.add_facet(
                    artifact_id=managed_artifact_id, kind="local_file", occurrence_id="artifacts.acquire_managed",
                    root_id=MANAGED_ROOT_ID, relative_path=acquired["managed_relative_path"],
                    byte_sha256=digest, byte_size=acquired["byte_size"], observed={"managed": True, "source_observation": acquired["observation"]},
                )
                existing = self.artifacts.register_managed_content(
                    content_sha256=digest, managed_artifact_id=managed_artifact_id,
                    byte_size=acquired["byte_size"], media_type=media_type,
                    format=fmt, storage_name=acquired["managed_relative_path"],
                )
            managed_artifact_id = existing["managed_artifact_id"]
            link = self.artifacts.link_managed_source(
                source_artifact_id=source_artifact["artifact_id"], content_sha256=digest,
                occurrence_id="artifacts.acquire_managed", source_root_id=facet["root_id"],
                source_relative_path=facet["relative_path"],
            )
            self.sources._verify_facet(source_root, facet["relative_path"], acquired["observation"])
            out = {
                "source_artifact_id": source_artifact["artifact_id"],
                "managed_artifact_id": managed_artifact_id,
                "content_sha256": digest, "byte_size": acquired["byte_size"],
                "format": existing["format"], "media_type": existing["media_type"],
                "detected_mime": verified["detected_mime"], "canonical_extension": extension,
                "extension_mismatch": verified["extension_mismatch"],
                "storage_name": existing["storage_name"], "reused": reused,
                "reused_storage": acquired["reused_storage"], "source_link_id": link["link_id"],
            }
            return ActionResult(True, out, {"ok": True, "operation": "acquire", "content_sha256": digest,
                                            "managed_artifact_id": managed_artifact_id, "reused": reused})
        except Exception as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "acquire"},
                                error_code="managed_intake_failed", error=str(exc))



def _magic_available() -> tuple[bool, str]:
    try:
        magic.from_buffer(b"Atlas", mime=True)
        return True, "available"
    except Exception as exc:
        return False, f"libmagic unavailable: {exc}"


def _safe_extension(relative_path: str) -> str:
    name = relative_path.rsplit("/", 1)[-1]
    parts = name.rsplit(".", 1)
    if len(parts) != 2:
        return ""
    suffix = parts[-1].lower()
    return f".{suffix}" if suffix.isalnum() and len(suffix) <= 10 else ""


_MIME_FORMATS = {
    "application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpeg",
    "image/gif": "gif", "image/webp": "webp", "application/zip": "zip",
    "application/json": "json", "application/xml": "xml", "text/xml": "xml",
    "text/html": "html", "text/csv": "csv", "text/markdown": "markdown",
    "application/msword": "doc", "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-word.document.macroenabled.12": "docm",
    "application/vnd.ms-excel.sheet.macroenabled.12": "xlsm",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12": "pptm",
}

def _verified_format(detected_mime: str, inspected_format: str, relative_path: str) -> tuple[str, str, str]:
    fmt = _MIME_FORMATS.get(detected_mime)
    if fmt is None and detected_mime.startswith("text/"):
        fmt = inspected_format if inspected_format in {"markdown", "html", "xml", "json", "csv", "text"} else "text"
    if fmt is None and detected_mime == "application/octet-stream":
        fmt = inspected_format if inspected_format in _FORMATS else "binary"
    if fmt in _FORMATS:
        extension, media_type = _FORMATS[fmt]
        # libmagic's MIME is the authority when it is more specific than generic text/octet-stream.
        if detected_mime not in {"application/octet-stream", "text/plain"}:
            media_type = detected_mime
        return fmt, extension, media_type
    extension = _safe_extension(relative_path) or ".bin"
    return fmt or "binary", extension, detected_mime or "application/octet-stream"
