from pathlib import Path

from atlas_api.compose import build_runtime
from atlas_core.provenance import InvocationProvenance


def test_library_scan_groups_exact_duplicates_without_mutating_sources(tmp_path: Path):
    instance = tmp_path / "instance"
    source_a = tmp_path / "source-a"; source_a.mkdir()
    source_b = tmp_path / "source-b"; source_b.mkdir()
    (source_a / "manual-one.pdf").write_bytes(b"same manual bytes")
    (source_b / "renamed-copy.pdf").write_bytes(b"same manual bytes")
    (source_b / "different.pdf").write_bytes(b"different document")

    rt = build_runtime(instance)
    rt.source_roots.put(root_id="a", host_path=str(source_a), display_name="A")
    rt.source_roots.put(root_id="b", host_path=str(source_b), display_name="B")
    rt.sources.reload(); rt.seed_policy()
    owner = rt.identities.current_owner().principal_id

    occurrence = rt.capabilities.invoke(
        "library.scan_duplicates", {"root_ids": ["a", "b"]},
        provenance=InvocationProvenance(owner, "human", "control"),
    )

    assert occurrence.status == "succeeded"
    summary = occurrence.result["scan"]["summary"]
    assert summary["files_scanned"] == 3
    assert summary["unique_files"] == 2
    assert summary["duplicate_copies"] == 1
    assert summary["duplicate_groups"] == 1


def test_library_materialize_copies_one_exact_canonical_set(tmp_path: Path):
    instance = tmp_path / "instance"
    source_a = tmp_path / "source-a"; source_a.mkdir()
    source_b = tmp_path / "source-b"; source_b.mkdir()
    (source_a / "manual.pdf").write_bytes(b"same")
    (source_b / "copy-renamed.pdf").write_bytes(b"same")
    (source_b / "other.pdf").write_bytes(b"other")

    rt = build_runtime(instance)
    rt.source_roots.put(root_id="a", host_path=str(source_a), display_name="A")
    rt.source_roots.put(root_id="b", host_path=str(source_b), display_name="B")
    rt.sources.reload(); rt.seed_policy()
    owner = rt.identities.current_owner().principal_id
    provenance = InvocationProvenance(owner, "human", "control")
    scan = rt.capabilities.invoke("library.scan_duplicates", {"root_ids": ["a", "b"]}, provenance=provenance)
    scan_id = scan.result["scan"]["scan_id"]

    materialized = rt.capabilities.invoke(
        "library.materialize", {"scan_id": scan_id, "destination_root_id": "atlas-library-clean"},
        provenance=provenance,
    )
    assert materialized.status == "succeeded"
    assert materialized.result["canonical_files"] == 2
    assert (instance / "library-clean" / "a" / "manual.pdf").read_bytes() == b"same"
    assert (instance / "library-clean" / "b" / "other.pdf").read_bytes() == b"other"
    assert not (instance / "library-clean" / "b" / "copy-renamed.pdf").exists()
    assert (source_b / "copy-renamed.pdf").exists()


def test_library_consolidation_runs_as_durable_work(tmp_path: Path):
    instance = tmp_path / "instance"
    source = tmp_path / "source"; source.mkdir()
    (source / "a.pdf").write_bytes(b"A")
    (source / "a-copy.pdf").write_bytes(b"A")
    rt = build_runtime(instance)
    rt.source_roots.put(root_id="manuals", host_path=str(source), display_name="Manuals")
    rt.sources.reload(); rt.seed_policy()
    owner = rt.identities.current_owner().principal_id
    work = rt.work.create(
        "Consolidate library",
        [
            {"capability_id": "library.scan_duplicates", "input": {"root_ids": ["manuals"], "max_files": 100}},
            {"capability_id": "library.materialize", "input": {
                "scan_id": {"$ref": {"step": 1, "output": "/scan/scan_id"}},
                "destination_root_id": "atlas-library-clean",
                "destination_relative_path": ".",
            }},
        ], owner_principal_id=owner,
    )
    result = rt.work.run(work.work_id)
    assert result["status"] == "completed"
    assert result["steps"][0]["status"] == "completed"
    assert result["steps"][1]["status"] == "completed"
    copied = list((instance / "library-clean" / "manuals").glob("*.pdf"))
    assert len(copied) == 1
