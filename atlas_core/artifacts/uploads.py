from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, ScopeResolution

UPLOAD_ROOT_ID = "atlas-owner-uploads"
UPLOAD_PROVIDER_NAMESPACE = "atlas-upload"
MAX_CHAT_UPLOAD_BYTES = 30 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


class OwnerUploadRuntime:
    """Stage request bytes, then govern their promotion into Atlas custody."""

    def __init__(self, *, staging_root: Path, upload_root: Path, sources, registry: CapabilityRegistry) -> None:
        self.staging_root = Path(staging_root)
        self.upload_root = Path(upload_root)
        self.sources = sources
        self.registry = registry
        self.staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.upload_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._cleanup_staging()
        self._register()

    @staticmethod
    def _filename(value: str) -> str:
        raw = Path(str(value or "upload")).name.strip() or "upload"
        clean = _SAFE.sub("_", raw).strip(" .") or "upload"
        return clean[:180]

    def stage(self, data: bytes, *, filename: str, media_type: str | None = None) -> dict[str, Any]:
        if not data:
            raise ValueError("upload is empty")
        if len(data) > MAX_CHAT_UPLOAD_BYTES:
            raise ValueError(f"upload exceeds {MAX_CHAT_UPLOAD_BYTES // (1024 * 1024)} MB")
        token = uuid4().hex
        path = self.staging_root / f"{token}.part"
        with path.open("xb") as handle:
            os.chmod(path, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        digest = hashlib.sha256(data).hexdigest()
        safe = self._filename(filename)
        return {
            "staging_token": token,
            "filename": safe,
            "destination_name": f"{token[:12]}-{safe}",
            "media_type": str(media_type or "application/octet-stream")[:160],
            "byte_size": len(data),
            "byte_sha256": digest,
        }

    def discard(self, token: str) -> None:
        try:
            self._staged_path(token).unlink()
        except FileNotFoundError:
            pass

    def _staged_path(self, token: str) -> Path:
        clean = str(token or "")
        if not re.fullmatch(r"[0-9a-f]{32}", clean):
            raise ValueError("invalid upload staging token")
        return self.staging_root / f"{clean}.part"

    def _cleanup_staging(self) -> None:
        for path in self.staging_root.glob("*.part"):
            try:
                path.unlink()
            except OSError:
                pass

    def _register(self) -> None:
        schema = {
            "type": "object",
            "required": ["staging_token", "filename", "destination_name", "media_type", "byte_size", "byte_sha256"],
            "properties": {
                "staging_token": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                "filename": {"type": "string", "minLength": 1, "maxLength": 180},
                "destination_name": {"type": "string", "minLength": 1, "maxLength": 220},
                "media_type": {"type": "string", "minLength": 1, "maxLength": 160},
                "byte_size": {"type": "integer", "minimum": 1, "maximum": MAX_CHAT_UPLOAD_BYTES},
                "byte_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
            "additionalProperties": False,
        }
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition(
                "artifacts.accept_upload",
                "Promote one staged owner upload into governed Atlas source custody and establish its Artifact identity.",
                "upload", "internal", schema, source="artifacts", tags=("artifacts", "upload", "chat"),
            ),
            self._scope,
            self._execute,
            metadata={
                "scope_hint": "files", "requires_owner_context": True,
                "work_composable": False, "model_visible": False,
            },
        ), replace=True)

    def _scope(self, payload: dict[str, Any]) -> ScopeResolution:
        destination = self._filename(payload.get("destination_name") or "")
        clean = dict(payload)
        clean["destination_name"] = destination
        scope = f"files/{UPLOAD_PROVIDER_NAMESPACE}/{UPLOAD_ROOT_ID}/{destination}"
        return ScopeResolution(scope, clean, f"Accept uploaded file {payload.get('filename') or destination}")

    def _execute(self, payload: dict[str, Any]) -> ActionResult:
        owner = str(payload.pop("__owner_principal_id", "") or "")
        payload.pop("__invocation_surface", None)
        token = str(payload.get("staging_token") or "")
        try:
            if not owner:
                raise ValueError("owner principal unavailable")
            source = self._staged_path(token)
            data = source.read_bytes()
            if len(data) != int(payload["byte_size"]):
                raise ValueError("staged upload size changed")
            if hashlib.sha256(data).hexdigest() != payload["byte_sha256"]:
                raise ValueError("staged upload hash changed")
            destination = self.upload_root / self._filename(payload["destination_name"])
            if destination.exists():
                raise ValueError("upload destination already exists")
            os.replace(source, destination)
            artifact = self.sources.establish_file(
                UPLOAD_ROOT_ID, destination.name, principal_id=owner,
                occurrence_hint="artifacts.accept_upload",
            )
            output = {
                **artifact, "original_name": payload["filename"],
                "media_type": payload["media_type"], "byte_size": payload["byte_size"],
                "byte_sha256": payload["byte_sha256"],
            }
            return ActionResult(True, output, {"ok": True, "operation": "upload", "artifact_id": artifact["artifact_id"]})
        except Exception as exc:
            try:
                self.discard(token)
            except Exception:
                pass
            return ActionResult(False, {}, {"ok": False, "operation": "upload"}, error_code="artifact_upload_failed", error=str(exc))