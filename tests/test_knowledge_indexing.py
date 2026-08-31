from __future__ import annotations

import pytest

from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.artifacts import ArtifactRuntime, ArtifactStore
from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.evidence import EvidenceStore
from atlas_core.identity import IdentityStore
from atlas_core.knowledge import KnowledgeRuntime, KnowledgeStore
from atlas_core.knowledge.generations import FTS_MECHANISM, GenerationStore, mechanism_input
from atlas_core.knowledge.indexing import IndexingRuntime
from atlas_core.knowledge.passages import PassageStore, content_hash
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.provenance import InvocationProvenance
from atlas_core.sources import SourceRootStore, SourceRuntime

MANUAL = """# LH517 Maintenance

The hydraulic pump service interval is 500 hours.

## Torque

Bolt torque is 42 Nm.
"""


class Harness:
    def __init__(self, tmp_path):
        identity_db = tmp_path / "identity.db"
        work_db = tmp_path / "work.db"
        self.identities = IdentityStore(identity_db); self.identities.initialize(owner_display_name="Jaco")
        self.owner = self.identities.current_owner()
        self.policy_store = PolicyStore(identity_db); self.policy_store.initialize()
        self.action_store = ActionStore(work_db); self.action_store.initialize()
        self.evidence = EvidenceStore(work_db); self.evidence.initialize()
        self.registry = CapabilityRegistry()
        policy = OwnerPolicy(self.policy_store)
        self.actions = ActionRuntime(policy=policy, store=self.action_store, evidence=self.evidence,
                                     executor_resolver=self.registry.executor)
        self.capabilities = CapabilityRuntime(self.registry, self.actions, policy)

        self.artifact_store = ArtifactStore(work_db); self.artifact_store.initialize()
        ArtifactRuntime(self.artifact_store, self.registry)

        self.root_dir = tmp_path / "root"; self.root_dir.mkdir()
        self.roots = SourceRootStore(identity_db); self.roots.initialize()
        self.roots.put(root_id="docs", host_path=str(self.root_dir), display_name="Docs")
        self.sources = SourceRuntime(self.roots, self.registry, self.artifact_store)

        self.knowledge_store = KnowledgeStore(work_db); self.knowledge_store.initialize()
        self.passages = PassageStore(work_db); self.passages.initialize()
        self.generations = GenerationStore(work_db); self.generations.initialize()
        self.indexing = IndexingRuntime(self.passages, self.generations, self.artifact_store, self.sources)
        self.knowledge = KnowledgeRuntime(self.knowledge_store, self.registry, self.indexing)

        for scope, operation in [("files/local/docs", "read"), ("files/local/docs", "extract_text"),
                                 ("atlas/knowledge/index", "index"), ("atlas/knowledge", "retrieve")]:
            self.allow(scope, operation, "YES")
        self.allow("atlas/knowledge/index", "activate", "CONFIRM")

    def allow(self, scope, operation, decision):
        self.policy_store.set(principal_id=self.owner.principal_id, scope=scope, operation=operation, decision=decision)

    def invoke(self, cid, payload, surface="control"):
        return self.capabilities.invoke(cid, payload, provenance=InvocationProvenance(self.owner.principal_id, "human", surface))

    def write(self, name, text=MANUAL):
        (self.root_dir / name).write_text(text)
        return name

    def extract(self, name):
        occurrence = self.invoke("files.extract_text", {"root_id": "docs", "relative_path": name})
        assert occurrence.status == "succeeded", occurrence.error
        return occurrence.result

    def index(self, extraction):
        occurrence = self.invoke("knowledge.index", {
            "source_artifact_id": extraction["artifact_id"],
            "extraction_artifact_id": extraction["extraction_artifact_id"],
        })
        assert occurrence.status == "succeeded", occurrence.error
        return occurrence.result

    def activate(self):
        generation = self.indexing.current_generation()
        receipt = self.indexing.verify(generation["generation_id"])
        assert receipt["ok"], receipt
        occurrence = self.invoke("knowledge.activate_generation", {"generation_id": generation["generation_id"]})
        assert occurrence.status == "pending_confirmation"
        confirmed = self.actions.confirm(occurrence.occurrence_id, principal_id=self.owner.principal_id)
        assert confirmed.status == "succeeded", confirmed.error
        return confirmed


def test_extraction_registers_a_derived_artifact_inside_the_managed_area(tmp_path):
    h = Harness(tmp_path)
    h.write("manual.md")
    result = h.extract("manual.md")

    assert result["derived_relative_path"].startswith(".atlas-derived/")
    assert (h.root_dir / result["derived_relative_path"]).is_file()

    extraction = h.artifact_store.get(result["extraction_artifact_id"])
    assert extraction["provenance"]["relation"] == "extracted_from"
    assert extraction["provenance"]["parents"] == [result["artifact_id"]]
    assert extraction["provenance"]["extractor_config_id"] == "extractor:markdown@1"
    facet = extraction["facets"][0]
    assert facet["kind"] == "local_file"
    assert facet["byte_sha256"] == result["text_sha256"]


def test_source_policy_no_blocks_extraction_at_the_files_gate(tmp_path):
    h = Harness(tmp_path)
    h.write("secret.md")
    h.allow("files/local/docs/secret.md", "extract_text", "NO")

    occurrence = h.invoke("files.extract_text", {"root_id": "docs", "relative_path": "secret.md"})
    assert occurrence.status == "blocked"
    assert occurrence.policy_decision == "NO"
    assert not list((h.root_dir / ".atlas-derived").iterdir())


def test_identical_content_in_two_artifacts_keeps_separate_placements(tmp_path):
    h = Harness(tmp_path)
    first = h.index(h.extract(h.write("manual-a.md")))
    second = h.index(h.extract(h.write("manual-b.md")))

    # Placement identity is per artifact; content identity is shared.
    assert first["new_contents"] == 2 and first["shared_contents"] == 0
    assert second["new_contents"] == 0 and second["shared_contents"] == 2
    assert first["source_artifact_id"] != second["source_artifact_id"]

    with h.passages._db() as db:
        assert db.execute("SELECT COUNT(*) FROM passage_contents").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM passages").fetchone()[0] == 4

    h.activate()
    rows = h.knowledge.retrieve("hydraulic pump service interval", limit=10, filters={"tiers": ["derived"]})
    artifacts = {row["grounding"]["artifact_id"] for row in rows}
    assert artifacts == {first["source_artifact_id"], second["source_artifact_id"]}
    for row in rows:
        assert row["grounding"]["locator"]["heading_path"] == ["LH517 Maintenance"]
        assert row["grounding"]["passage_id"]
        assert row["grounding"]["content_hash"] == content_hash(row["content"])


def test_indexing_is_idempotent_and_refuses_sources_outside_the_derived_area(tmp_path):
    h = Harness(tmp_path)
    extraction = h.extract(h.write("manual.md"))
    first = h.index(extraction)
    second = h.index(extraction)
    assert first["passages"] == second["passages"] == 2
    with h.passages._db() as db:
        assert db.execute("SELECT COUNT(*) FROM passages").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM passage_fts").fetchone()[0] == 2

    # An artifact whose only representation sits outside the managed derived area
    # never becomes indexable: index reads ride the extraction grant.
    h.write("loose.md")
    outside = h.artifact_store.register(principal_id=h.owner.principal_id, display_name="loose.md", occurrence_id="test")
    h.artifact_store.add_facet(artifact_id=outside, kind="local_file", occurrence_id="test",
                               root_id="docs", relative_path="loose.md", byte_sha256="deadbeef")
    refused = h.invoke("knowledge.index", {
        "source_artifact_id": extraction["artifact_id"], "extraction_artifact_id": outside,
    })
    assert refused.status == "failed"
    assert refused.error_code == "knowledge_index_source_invalid"


def test_generation_lifecycle_gates_the_default_corpus(tmp_path):
    h = Harness(tmp_path)
    h.index(h.extract(h.write("manual.md")))
    generation = h.indexing.current_generation()
    assert generation["state"] == "building"

    # A building generation is not the default corpus.
    assert h.knowledge.retrieve("hydraulic pump", limit=5, filters={"tiers": ["derived"]}) == []
    # ...but is reachable as an explicit candidate once verified.
    h.indexing.verify(generation["generation_id"])
    assert h.indexing.retrieve("hydraulic pump", limit=5, generation="candidate")

    h.activate()
    assert h.generations.get(generation["generation_id"])["state"] == "active"
    assert h.knowledge.retrieve("hydraulic pump", limit=5, filters={"tiers": ["derived"]})

    # Exactly one generation may be active, enforced in SQL.
    second = h.generations.create(extractor_config_id="extractor:text@1",
                                  segmenter_config_id="segmenter:headings@1",
                                  mechanisms=[FTS_MECHANISM], occurrence_id="test")
    with pytest.raises(Exception):
        h.generations.set_state(second, "active")


def test_physical_index_is_disposable_and_rebuildable(tmp_path):
    h = Harness(tmp_path)
    h.index(h.extract(h.write("manual.md")))
    h.activate()
    before = h.knowledge.retrieve("bolt torque", limit=5, filters={"tiers": ["derived"]})
    assert before

    rebuilt = h.passages.rebuild_fts()
    assert rebuilt == 2
    after = h.knowledge.retrieve("bolt torque", limit=5, filters={"tiers": ["derived"]})
    assert [row["grounding"]["passage_id"] for row in after] == [row["grounding"]["passage_id"] for row in before]


def test_retrieval_merges_tiers_by_rank_without_fusing_scores(tmp_path):
    h = Harness(tmp_path)
    h.index(h.extract(h.write("manual.md")))
    h.activate()
    h.knowledge.promote(content="Pump overhaul was completed in March.", title="Pump overhaul",
                        source_ref="artifact:external")

    rows = h.knowledge.retrieve("pump", limit=10)
    tiers = [row["grounding"]["tier"] for row in rows]
    assert "curated" in tiers and "derived" in tiers
    assert tiers[0] != tiers[1]
    assert {row["mechanism"] for row in rows} == {"fts.bm25@curated", "fts.bm25@passages"}


def test_representation_key_uses_the_complete_mechanism_input(tmp_path):
    pump = {"locator": {"heading_path": ["Pump", "Torque"]}}
    gearbox = {"locator": {"heading_path": ["Gearbox", "Torque"]}}
    shared = "Bolt torque is 42 Nm."
    content_only = {"template": "content-only@1", "fields": ["content"]}
    heading_aware = {"template": "heading-aware@1", "fields": ["heading_path", "content"]}

    # Content-only assembly collapses to content identity: maximal reuse.
    assert mechanism_input(pump, shared, content_only)[1] == mechanism_input(gearbox, shared, content_only)[1]
    # Placement-aware assembly must distinguish placements that merely share content.
    assert mechanism_input(pump, shared, heading_aware)[1] != mechanism_input(gearbox, shared, heading_aware)[1]
    # A template change invalidates exactly the representations whose input changed.
    assert mechanism_input(pump, shared, content_only)[1] != mechanism_input(pump, shared, heading_aware)[1]


def test_passive_verification_marks_changed_bytes_stale_and_keeps_them_stale(tmp_path):
    h = Harness(tmp_path)
    name = h.write("manual.md")
    first = h.extract(name)
    facet = h.artifact_store.find_local("docs", name)
    assert facet["state"] == "present"
    assert facet["verified_at"]

    (h.root_dir / name).write_text(MANUAL + "\n\nAdded a revision line.\n")
    reread = h.invoke("files.read", {"root_id": "docs", "relative_path": name})
    assert reread.status == "succeeded"
    stale = h.artifact_store.find_local("docs", name)
    assert stale["state"] == "stale"
    # The recorded hash is the identity we registered: observing different bytes
    # must not overwrite it, or the change itself becomes invisible.
    assert stale["byte_sha256"] == facet["byte_sha256"]

    # Staleness is sticky. Re-extraction yields a NEW extraction artifact, and the
    # source facet stays stale until revision handling decides what the new bytes
    # are: silently re-verifying would erase the fact that the bytes changed.
    second = h.extract(name)
    assert second["extraction_artifact_id"] != first["extraction_artifact_id"]
    assert second["text_sha256"] != first["text_sha256"]
    assert h.artifact_store.find_local("docs", name)["state"] == "stale"


def test_new_intake_forks_active_generation_and_never_mutates_active_or_candidate(tmp_path):
    h = Harness(tmp_path)
    first = h.index(h.extract(h.write("manual-a.md")))
    h.activate()
    active_id = first["generation_id"]

    second_extraction = h.extract(h.write("manual-b.md", "# Second\nPressure is 95 bar.\n"))
    second = h.index(second_extraction)
    assert second["generation_id"] != active_id
    assert h.generations.get(active_id)["state"] == "active"
    with h.passages._db() as db:
        old_count = db.execute("SELECT COUNT(*) FROM generation_passages WHERE generation_id=?", (active_id,)).fetchone()[0]
        new_count = db.execute("SELECT COUNT(*) FROM generation_passages WHERE generation_id=?", (second["generation_id"],)).fetchone()[0]
    assert new_count > old_count

    refused_active = h.invoke("knowledge.index", {
        "source_artifact_id": second_extraction["artifact_id"],
        "extraction_artifact_id": second_extraction["extraction_artifact_id"],
        "generation_id": active_id,
    })
    assert refused_active.status == "failed"
    assert "only a building generation accepts passages" in (refused_active.error or "")

    h.indexing.verify(second["generation_id"])
    refused_candidate = h.invoke("knowledge.index", {
        "source_artifact_id": second_extraction["artifact_id"],
        "extraction_artifact_id": second_extraction["extraction_artifact_id"],
        "generation_id": second["generation_id"],
    })
    assert refused_candidate.status == "failed"
    assert "only a building generation accepts passages" in (refused_candidate.error or "")
