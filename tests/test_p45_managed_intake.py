from __future__ import annotations

import hashlib
import json
from pathlib import Path

from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.artifacts import (
    ArtifactRuntime, ArtifactStore, ManagedIntakeRuntime, ArtifactIntakeRuntime, ArtifactIntakeStore,
)
from atlas_core.artifacts.managed import MANAGED_PROVIDER_NAMESPACE, MANAGED_ROOT_ID
from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.evidence import EvidenceStore
from atlas_core.identity import IdentityStore
from atlas_core.knowledge import KnowledgeRuntime, KnowledgeStore
from atlas_core.knowledge.generations import GenerationStore
from atlas_core.knowledge.indexing import IndexingRuntime
from atlas_core.knowledge.passages import PassageStore
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.providers import ModelResponse
from atlas_core.provenance import InvocationProvenance
from atlas_core.sources import SourceRootStore, SourceRuntime
from atlas_core.work import WorkRuntime, WorkStore


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        identity_db, work_db = tmp_path / "identity.db", tmp_path / "work.db"
        self.work_db = work_db
        ids = IdentityStore(identity_db); ids.initialize(owner_display_name="Owner"); self.owner = ids.current_owner()
        self.policy_store = PolicyStore(identity_db); self.policy_store.initialize(); policy = OwnerPolicy(self.policy_store)
        self.action_store = ActionStore(work_db); self.action_store.initialize(); evidence = EvidenceStore(work_db); evidence.initialize()
        self.registry = CapabilityRegistry()
        actions = ActionRuntime(policy=policy, store=self.action_store, evidence=evidence, executor_resolver=self.registry.executor)
        self.capabilities = CapabilityRuntime(self.registry, actions, policy)
        self.artifacts = ArtifactStore(work_db); self.artifacts.initialize()
        self.source_root = tmp_path / "source"; self.source_root.mkdir()
        self.managed_root = tmp_path / "managed"; self.managed_root.mkdir()
        roots = SourceRootStore(identity_db); roots.initialize()
        roots.put(root_id="manuals", host_path=str(self.source_root), display_name="Manuals")
        roots.put(root_id=MANAGED_ROOT_ID, host_path=str(self.managed_root), display_name="Managed",
                  provider_namespace=MANAGED_PROVIDER_NAMESPACE, quarantine_relative_path=None)
        self.sources = SourceRuntime(roots, self.registry, self.artifacts)
        ArtifactRuntime(self.artifacts, self.registry, self.sources)
        self.managed_intake = ManagedIntakeRuntime(self.artifacts, self.sources, self.registry)
        for scope in ("files/local/manuals",):
            for op in ("diff", "verify_format", "acquire", "intake"):
                self.policy_store.set(principal_id=self.owner.principal_id, scope=scope, operation=op, decision="YES")

    def invoke(self, capability_id: str, payload: dict):
        return self.capabilities.invoke(
            capability_id, payload,
            provenance=InvocationProvenance(self.owner.principal_id, "human", "control"),
        )

    def discover(self, name: str) -> str:
        diff = self.invoke("artifacts.diff_source", {"root_id": "manuals"})
        assert diff.status == "succeeded", diff.error
        return next(row["artifact_id"] for row in diff.result["new"] if row["relative_path"] == name)

def test_managed_intake_copies_without_mutating_original_and_uses_canonical_name(tmp_path):
    h = Harness(tmp_path)
    payload = b"%PDF-1.7\nmanual bytes\n%%EOF\n"
    original = h.source_root / "LH410 MANUAL final FINAL(3).PDF"
    original.write_bytes(payload)
    artifact_id = h.discover(original.name)

    result = h.invoke("artifacts.acquire_managed", {"artifact_id": artifact_id})
    assert result.status == "succeeded", result.error
    digest = hashlib.sha256(payload).hexdigest()
    assert result.result["content_sha256"] == digest
    assert result.result["storage_name"] == f"sha256-{digest}.pdf"
    assert original.read_bytes() == payload
    assert (h.managed_root / result.result["storage_name"]).read_bytes() == payload



def test_format_verification_uses_magic_bytes_and_drives_canonical_extension(tmp_path):
    h = Harness(tmp_path)
    payload = b"%PDF-1.7\nspoofed extension\n%%EOF\n"
    original = h.source_root / "looks-like-a-photo.jpg"
    original.write_bytes(payload)
    artifact_id = h.discover(original.name)

    verified = h.invoke("artifacts.verify_format", {"artifact_id": artifact_id})
    assert verified.status == "succeeded", verified.error
    assert verified.result["detected_mime"] == "application/pdf"
    assert verified.result["format"] == "pdf"
    assert verified.result["canonical_extension"] == ".pdf"
    assert verified.result["extension_mismatch"] is True

    acquired = h.invoke("artifacts.acquire_managed", {"artifact_id": artifact_id})
    assert acquired.status == "succeeded", acquired.error
    assert acquired.result["storage_name"].endswith(".pdf")
    assert acquired.result["extension_mismatch"] is True

def test_exact_duplicate_source_occurrences_reuse_one_managed_artifact(tmp_path):
    h = Harness(tmp_path)
    payload = b"%PDF-1.7\nsame technical manual\n%%EOF\n"
    first = h.source_root / "manual-one.pdf"; first.write_bytes(payload)
    first_id = h.discover(first.name)
    acquired_first = h.invoke("artifacts.acquire_managed", {"artifact_id": first_id})
    assert acquired_first.status == "succeeded"

    second = h.source_root / "renamed backup FINAL.pdf"; second.write_bytes(payload)
    second_id = h.discover(second.name)
    acquired_second = h.invoke("artifacts.acquire_managed", {"artifact_id": second_id})
    assert acquired_second.status == "succeeded"
    assert acquired_second.result["managed_artifact_id"] == acquired_first.result["managed_artifact_id"]
    assert acquired_second.result["reused"] is True
    managed = h.artifacts.get(acquired_first.result["managed_artifact_id"])
    assert len(managed["source_occurrences"]) == 2


class IntakeProvider:
    def __init__(self) -> None:
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ModelResponse(json.dumps({
            "artifact_class": "A", "purpose": "durable technical reference",
            "knowledge_disposition": "ingest", "relationship": "new", "creates_work": True,
            "workflow_class": "A", "workflow_intent": "knowledge.ingest", "confidence": 0.98,
            "inspection_sufficiency": "sufficient", "unresolved_questions": [],
            "representation_needs": [], "reason": "The manual should be durable Knowledge.",
        }), "stub", "stub-model", {})


class IntakeHarness(Harness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        ks = KnowledgeStore(self.work_db); ks.initialize()
        passages = PassageStore(self.work_db); passages.initialize()
        generations = GenerationStore(self.work_db); generations.initialize()
        indexing = IndexingRuntime(passages, generations, self.artifacts, self.sources)
        KnowledgeRuntime(ks, self.registry, indexing)
        work_store = WorkStore(self.work_db); work_store.initialize()
        self.work_store = work_store
        work = WorkRuntime(work_store, self.capabilities, self.action_store)
        self.provider = IntakeProvider()
        intake_store = ArtifactIntakeStore(self.work_db); intake_store.initialize()
        self.intake = ArtifactIntakeRuntime(
            intake_store, self.artifacts, self.provider, work, self.registry, self.capabilities,
            managed_intake=self.managed_intake,
        )
        for scope, op in [
            ("atlas/artifacts/intake", "classify"),
            (f"files/{MANAGED_PROVIDER_NAMESPACE}/{MANAGED_ROOT_ID}", "inspect"),
        ]:
            self.policy_store.set(principal_id=self.owner.principal_id, scope=scope, operation=op, decision="YES")


def test_intake_workflow_runs_registered_custody_stages_before_semantic_routing(tmp_path):
    h = IntakeHarness(tmp_path)
    source = h.source_root / "manual-disguised.jpg"
    source.write_bytes(b"%PDF-1.7\nworkflow manual\n%%EOF\n")
    artifact_id = h.discover(source.name)

    assert h.intake.intake_workflow.available() == (
        "artifacts.verify_format", "artifacts.acquire_managed", "artifacts.inspect",
    )
    for capability_id in h.intake.intake_workflow.available():
        assert h.registry.get(capability_id).definition.id == capability_id

    routed = h.invoke("artifacts.classify_intake", {
        "artifact_id": artifact_id, "source_event_kind": "new",
    })
    assert routed.status == "succeeded", routed.error
    assert routed.result["intake_pipeline"]["stages"] == list(h.intake.intake_workflow.available())
    assert list(routed.result["intake_pipeline"]["occurrence_ids"]) == list(h.intake.intake_workflow.available())
    assert routed.result["acquisition"]["format_verification"]["detected_mime"] == "application/pdf"
    assert routed.result["work"]["status"] == "queued"
    assert len(h.provider.requests) == 1


def test_intake_file_capability_runs_establish_custody_inspection_and_routing(tmp_path):
    h = IntakeHarness(tmp_path)
    payload = b"%PDF-1.7\nselected manual\n%%EOF\n"
    source = h.source_root / "selected-manual.pdf"
    source.write_bytes(payload)

    result = h.invoke("artifacts.intake_file", {"root_id": "manuals", "relative_path": source.name})
    assert result.status == "succeeded", result.error
    assert result.result["established"]["relative_path"] == source.name
    assert result.result["source_artifact_id"] == result.result["established"]["artifact_id"]
    assert result.result["managed_artifact_id"]
    assert result.result["acquisition"]["storage_name"].endswith(".pdf")
    assert result.result["inspection"]["format"] == "pdf"
    assert result.result["work"]["display_ref"] == "AA-001"
    assert result.result["work"]["status"] == "queued"
    assert len(h.provider.requests) == 1


def test_semantic_intake_retries_once_when_model_json_misses_required_fields(tmp_path):
    h = IntakeHarness(tmp_path)
    source = h.source_root / "retry-manual.pdf"
    source.write_bytes(b"%PDF-1.7\nretry manual\n%%EOF\n")

    good = h.provider.generate
    calls = {"count": 0}
    def flaky(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return ModelResponse('{"purpose":"manual"}', "stub", "stub-model", {})
        return good(request)
    h.provider.generate = flaky

    result = h.invoke("artifacts.intake_file", {"root_id": "manuals", "relative_path": source.name})
    assert result.status == "succeeded", result.error
    assert calls["count"] == 2
    assert result.result["classification"]["artifact_class"] == "A"
    assert result.result["work"]["display_ref"] == "AA-001"
