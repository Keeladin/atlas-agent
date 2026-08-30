from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

FacetKind = Literal["local_file", "remote_resource"]
FacetState = Literal["present", "stale", "missing"]


@dataclass(frozen=True)
class ArtifactFacet:
    """One governed representation of an artifact's content."""

    facet_id: str
    artifact_id: str
    kind: FacetKind
    state: FacetState
    occurrence_id: str
    root_id: str | None = None
    relative_path: str | None = None
    byte_sha256: str | None = None
    byte_size: int | None = None
    provider: str | None = None
    external_id: str | None = None
    locator: str | None = None
    observed: dict[str, Any] = field(default_factory=dict)
    verified_at: str | None = None
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "facet_id": self.facet_id, "artifact_id": self.artifact_id, "kind": self.kind,
            "state": self.state, "occurrence_id": self.occurrence_id,
            "root_id": self.root_id, "relative_path": self.relative_path,
            "byte_sha256": self.byte_sha256, "byte_size": self.byte_size,
            "provider": self.provider, "external_id": self.external_id, "locator": self.locator,
            "observed": self.observed, "verified_at": self.verified_at, "created_at": self.created_at,
        }


@dataclass(frozen=True)
class Artifact:
    """Durable content identity. Provenance cites the occurrence that established it."""

    artifact_id: str
    principal_id: str
    display_name: str
    media_type: str | None
    provenance: dict[str, Any]
    occurrence_id: str
    created_at: str
    facets: tuple[ArtifactFacet, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id, "principal_id": self.principal_id,
            "display_name": self.display_name, "media_type": self.media_type,
            "provenance": self.provenance, "occurrence_id": self.occurrence_id,
            "created_at": self.created_at, "facets": [facet.as_dict() for facet in self.facets],
        }
