from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SOURCE_EVIDENCE_PROVENANCE = frozenset({"acquired_observation", "acquired_content"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OBSERVATION_KINDS = {"metadata", "hash", "content", "listing", "listing_entry"}
_OBJECT_TYPES = {
    "regular_file", "directory", "symlink", "socket", "fifo",
    "block_device", "character_device", "unknown",
}
_CONSISTENCY = {"stable", "metadata_only", "drifted", "provider_asserted"}
_COMPLETENESS = {"metadata_only", "complete", "bounded", "truncated"}


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _source_ref(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "kind", "provider_namespace", "source_id", "root_id",
        "relative_path", "display_locator",
    }
    return (
        set(value) == required
        and value.get("kind") == "local_file"
        and all(isinstance(value.get(key), str) and value[key] for key in required)
    )


def valid_source_observation_payload(payload: Any) -> bool:
    """Validate the persisted Files observation envelope and its payload digest."""

    if not isinstance(payload, dict) or not isinstance(payload.get("observation"), dict):
        return False
    observation = payload["observation"]
    required = {
        "observation_id", "observation_payload_sha256", "source_ref", "observed_at",
        "observation_kind", "object_type", "consistency", "completeness",
        "byte_size", "byte_sha256", "media_type", "media_type_source", "metadata",
        "acquisition",
    }
    if set(observation) != required:
        return False
    if not isinstance(observation["observation_id"], str) or not observation["observation_id"]:
        return False
    if not _sha256(observation["observation_payload_sha256"]):
        return False
    if not _source_ref(observation["source_ref"]):
        return False
    if not isinstance(observation["observed_at"], str) or not observation["observed_at"]:
        return False
    if observation["observation_kind"] not in _OBSERVATION_KINDS:
        return False
    if observation["object_type"] not in _OBJECT_TYPES:
        return False
    if observation["consistency"] not in _CONSISTENCY:
        return False
    if observation["completeness"] not in _COMPLETENESS:
        return False
    if observation["byte_size"] is not None and (
        not isinstance(observation["byte_size"], int) or observation["byte_size"] < 0
    ):
        return False
    if observation["byte_sha256"] is not None and not _sha256(observation["byte_sha256"]):
        return False
    if not isinstance(observation["metadata"], dict) or not isinstance(observation["acquisition"], dict):
        return False
    acquisition = observation["acquisition"]
    if not all(
        isinstance(acquisition.get(key), str) and acquisition[key]
        for key in (
            "provider_namespace", "root_id", "configuration_revision", "operation",
            "filesystem_policy_version", "backend",
        )
    ):
        return False
    digest_payload = dict(observation)
    digest_payload.pop("observation_id")
    digest_payload.pop("observation_payload_sha256")
    encoded = json.dumps(
        digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest() == observation["observation_payload_sha256"]


def valid_acquired_content_payload(payload: Any) -> bool:
    """Validate the controlled UTF-8 content/reference envelope emitted by files.read."""

    if not isinstance(payload, dict):
        return False
    required = {
        "text", "encoding", "bom", "media_type", "source_observation_id",
        "source_observation_payload_sha256", "source_byte_sha256", "source_ref",
    }
    return (
        set(payload) == required
        and isinstance(payload["text"], str)
        and payload["encoding"] == "utf-8"
        and isinstance(payload["bom"], bool)
        and (payload["media_type"] is None or isinstance(payload["media_type"], str))
        and isinstance(payload["source_observation_id"], str)
        and bool(payload["source_observation_id"])
        and _sha256(payload["source_observation_payload_sha256"])
        and _sha256(payload["source_byte_sha256"])
        and _source_ref(payload["source_ref"])
    )


def qualifies_as_source_evidence(artifact: Any) -> bool:
    """Artifact provenance and acquisition state, independent of kind names."""

    category = artifact.provenance_category
    if category not in SOURCE_EVIDENCE_PROVENANCE:
        return False
    metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
    consistency = metadata.get("source_consistency")
    if consistency == "drifted":
        return False
    payload = artifact.payload
    if category == "acquired_observation":
        if not valid_source_observation_payload(payload):
            return False
        return payload["observation"]["consistency"] != "drifted"
    if not valid_acquired_content_payload(payload):
        return False
    return (
        consistency in {"stable", "provider_asserted"}
        and metadata.get("source_observation_id") == payload["source_observation_id"]
    )
