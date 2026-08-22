from __future__ import annotations

from typing import Any


SOURCE_EVIDENCE_PROVENANCE = frozenset({"acquired_observation", "acquired_content"})


def qualifies_as_source_evidence(artifact: Any) -> bool:
    """Artifact provenance and acquisition state, independent of kind names."""

    if artifact.provenance_category not in SOURCE_EVIDENCE_PROVENANCE:
        return False
    consistency = artifact.metadata.get("source_consistency")
    if consistency == "drifted":
        return False
    payload = artifact.payload
    if artifact.provenance_category == "acquired_observation" and isinstance(payload, dict):
        observation = payload.get("observation", payload)
        if isinstance(observation, dict) and observation.get("consistency") == "drifted":
            return False
    return True
