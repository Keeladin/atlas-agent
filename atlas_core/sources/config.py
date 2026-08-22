from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .local import LocalRootConfig, LocalRootRegistry, LocalSourceKernel


@dataclass
class LocalSourceDeployment:
    registry: LocalRootRegistry
    kernel: LocalSourceKernel

    def public_state(self) -> tuple[dict[str, Any], ...]:
        return self.registry.execution_policies()

    def close(self) -> None:
        self.registry.close()


def load_local_source_deployment(config_path: str | Path) -> LocalSourceDeployment:
    path = Path(config_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Deployment configuration cannot be loaded") from exc
    if not isinstance(payload, dict):
        raise ValueError("Deployment configuration must be an object")
    raw_roots = payload.get("local_source_roots", [])
    if not isinstance(raw_roots, list):
        raise ValueError("local_source_roots must be an array")

    registry = LocalRootRegistry()
    try:
        for index, raw in enumerate(raw_roots, start=1):
            registry.register(_root_config(raw, index=index))
    except BaseException:
        registry.close()
        raise
    return LocalSourceDeployment(registry, LocalSourceKernel(registry))


def _root_config(raw: Any, *, index: int) -> LocalRootConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"local_source_roots entry {index} must be an object")
    allowed = {
        "provider_namespace", "root_id", "host_path", "display_name",
        "read_allowed", "mutation_allowed", "allow_cross_mounts",
    }
    unexpected = set(raw) - allowed
    if unexpected:
        raise ValueError(
            f"local_source_roots entry {index} has unsupported fields: "
            + ", ".join(sorted(unexpected))
        )
    provider_namespace = _required_text(raw, "provider_namespace", index)
    root_id = _required_text(raw, "root_id", index)
    host_path = _required_text(raw, "host_path", index)
    display_name = raw.get("display_name")
    if display_name is not None and not isinstance(display_name, str):
        raise ValueError(f"local_source_roots entry {index} display_name must be text")
    read_allowed = _boolean(raw, "read_allowed", True, index)
    mutation_allowed = _boolean(raw, "mutation_allowed", False, index)
    allow_cross_mounts = _boolean(raw, "allow_cross_mounts", False, index)
    if not os.path.isabs(host_path):
        raise ValueError(f"local_source_roots entry {index} host_path must be absolute")
    try:
        info = os.lstat(host_path)
    except OSError as exc:
        raise ValueError(
            f"local_source_roots entry {index} target is unavailable"
        ) from exc
    canonical = os.path.realpath(host_path)
    revision_payload = {
        "revision_format": "local-root-security-v1",
        "provider_namespace": provider_namespace,
        "root_id": root_id,
        "canonical_host_path": canonical,
        "device": info.st_dev,
        "inode": info.st_ino,
        "read_allowed": read_allowed,
        "mutation_allowed": mutation_allowed,
        "allow_cross_mounts": allow_cross_mounts,
    }
    encoded = json.dumps(
        revision_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    revision = "local-root-v1-" + hashlib.sha256(encoded).hexdigest()
    return LocalRootConfig(
        provider_namespace=provider_namespace,
        root_id=root_id,
        host_path=host_path,
        display_name=display_name,
        read_allowed=read_allowed,
        mutation_allowed=mutation_allowed,
        allow_cross_mounts=allow_cross_mounts,
        configuration_revision=revision,
    )


def _required_text(raw: dict[str, Any], key: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"local_source_roots entry {index} {key} must be non-empty text")
    return value.strip()


def _boolean(raw: dict[str, Any], key: str, default: bool, index: int) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"local_source_roots entry {index} {key} must be boolean")
    return value
