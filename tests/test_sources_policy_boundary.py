from __future__ import annotations

import os

import pytest

from atlas_api.compose import build_runtime
from atlas_core.provenance import InvocationProvenance


def test_source_root_scope_is_canonical_and_traversal_fails(tmp_path):
    instance = tmp_path / "instance"
    root = tmp_path / "root"
    root.mkdir()
    (root / "manual.txt").write_text("verified manual", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    os.symlink(outside, root / "escape.txt")

    rt = build_runtime(instance)
    owner = rt.identities.current_owner().principal_id
    rt.source_roots.put(root_id="manuals", host_path=str(root), display_name="Manuals")
    rt.sources.reload()
    rt.seed_policy()
    provenance = InvocationProvenance(owner, "human", "chat")

    listing = rt.capabilities.invoke(
        "files.list", {"root_id": "manuals", "relative_path": "."},
        provenance=provenance,
    )
    assert listing.status == "succeeded"
    assert listing.scope == "files/local/manuals"

    read = rt.capabilities.invoke(
        "files.read", {"root_id": "manuals", "relative_path": "manual.txt"},
        provenance=provenance,
    )
    assert read.status == "succeeded"
    assert read.result["content"]["text"] == "verified manual"

    with pytest.raises(Exception):
        rt.capabilities.invoke(
            "files.read", {"root_id": "manuals", "relative_path": "../outside.txt"},
            provenance=provenance,
        )

    escaped = rt.capabilities.invoke(
        "files.read", {"root_id": "manuals", "relative_path": "escape.txt"},
        provenance=provenance,
    )
    assert escaped.status == "failed"
    assert escaped.error_code in {"symlink_rejected", "files_error"}

    with pytest.raises(Exception):
        rt.capabilities.invoke(
            "files.copy",
            {"root_id": "manuals", "relative_path": "manual.txt", "destination_path": "../copy.txt"},
            provenance=provenance,
        )


def test_source_ids_cannot_smuggle_policy_segments(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError):
        rt.source_roots.put(root_id="../escape", host_path=str(root))
