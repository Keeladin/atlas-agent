from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4


ObjectType = Literal[
    "regular_file",
    "directory",
    "symlink",
    "socket",
    "fifo",
    "block_device",
    "character_device",
    "unknown",
    "missing",
]
ObservationKind = Literal["metadata", "hash", "content", "listing", "listing_entry", "absence"]
Consistency = Literal["stable", "metadata_only", "drifted", "provider_asserted"]
Completeness = Literal["metadata_only", "complete", "bounded", "truncated"]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class SourceRef:
    """Logical provider-owned source identity, not an access grant or observation."""

    kind: Literal["local_file"]
    provider_namespace: str
    source_id: str
    root_id: str
    relative_path: str
    display_locator: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "provider_namespace": self.provider_namespace,
            "source_id": self.source_id,
            "root_id": self.root_id,
            "relative_path": self.relative_path,
            "display_locator": self.display_locator,
        }


@dataclass(frozen=True)
class SourceObservation:
    """Immutable record of one acquisition event."""

    observation_id: str
    observation_payload_sha256: str
    source_ref: SourceRef
    observed_at: str
    observation_kind: ObservationKind
    object_type: ObjectType
    consistency: Consistency
    completeness: Completeness
    byte_size: int | None = None
    byte_sha256: str | None = None
    media_type: str | None = None
    media_type_source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    acquisition: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        source_ref: SourceRef,
        observed_at: str,
        observation_kind: ObservationKind,
        object_type: ObjectType,
        consistency: Consistency,
        completeness: Completeness,
        byte_size: int | None = None,
        byte_sha256: str | None = None,
        media_type: str | None = None,
        media_type_source: str | None = None,
        metadata: dict[str, Any] | None = None,
        acquisition: dict[str, Any] | None = None,
        observation_id: str | None = None,
    ) -> SourceObservation:
        payload = {
            "source_ref": source_ref.to_dict(),
            "observed_at": observed_at,
            "observation_kind": observation_kind,
            "object_type": object_type,
            "consistency": consistency,
            "completeness": completeness,
            "byte_size": byte_size,
            "byte_sha256": byte_sha256,
            "media_type": media_type,
            "media_type_source": media_type_source,
            "metadata": metadata or {},
            "acquisition": acquisition or {},
        }
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        return cls(
            observation_id=observation_id or f"obs_{uuid4().hex}",
            observation_payload_sha256=digest,
            source_ref=source_ref,
            observed_at=observed_at,
            observation_kind=observation_kind,
            object_type=object_type,
            consistency=consistency,
            completeness=completeness,
            byte_size=byte_size,
            byte_sha256=byte_sha256,
            media_type=media_type,
            media_type_source=media_type_source,
            metadata=metadata or {},
            acquisition=acquisition or {},
        )

    def payload_dict(self) -> dict[str, Any]:
        result = self.to_dict()
        result.pop("observation_id")
        result.pop("observation_payload_sha256")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "observation_payload_sha256": self.observation_payload_sha256,
            "source_ref": self.source_ref.to_dict(),
            "observed_at": self.observed_at,
            "observation_kind": self.observation_kind,
            "object_type": self.object_type,
            "consistency": self.consistency,
            "completeness": self.completeness,
            "byte_size": self.byte_size,
            "byte_sha256": self.byte_sha256,
            "media_type": self.media_type,
            "media_type_source": self.media_type_source,
            "metadata": self.metadata,
            "acquisition": self.acquisition,
        }
