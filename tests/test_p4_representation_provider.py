from __future__ import annotations

import json
import os

import fitz

from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.artifacts import ArtifactRuntime, ArtifactStore, ArtifactIntakeRuntime, ArtifactIntakeStore
from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.evidence import EvidenceStore
from atlas_core.identity import IdentityStore
from atlas_core.knowledge import KnowledgeRuntime, KnowledgeStore
from atlas_core.knowledge.generations import GenerationStore
from atlas_core.knowledge.indexing import IndexingRuntime
from atlas_core.knowledge.passages import PassageStore
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.provenance import InvocationProvenance
from atlas_core.providers import ModelResponse
from atlas_core.representations import RepresentationRuntime, SubprocessRepresentationProvider
from atlas_core.sources import SourceRootStore, SourceRuntime
from atlas_core.work import WorkRuntime, WorkStore


class IntakeProvider:
    def __init__(self): self.requests=[]
    def generate(self, request):
        self.requests.append(request)
        return ModelResponse(json.dumps({
            "artifact_class":"A", "purpose":"durable technical reference",
            "knowledge_disposition":"ingest", "relationship":"new", "creates_work":True,
            "workflow_class":"A", "workflow_intent":"knowledge.ingest", "confidence":0.98,
            "inspection_sufficiency":"partial", "unresolved_questions":["visual semantics remain unresolved"],
            "representation_needs":["text"], "reason":"The manual requires a durable searchable text representation."
        }), "stub", "stub-model", {})


def _provider_script(tmp_path):
    path=tmp_path/"ocr_provider.py"
    path.write_text('''#!/usr/bin/env python3\nimport json, os, sys\nraw=sys.stdin.buffer.read()\nassert raw.startswith(b"%PDF-")\nassert os.environ["ATLAS_REPRESENTATION_NEED"] == "ocr"\nassert "ATLAS_SECRET_SENTINEL" not in os.environ\nprint(json.dumps({"text":"OCR page 1\\nService interval is 900 operating hours.","media_type":"text/plain","metadata":{"pages":1},"provider_version":"test-ocr@1"}))\n''')
    path.chmod(0o755)
    return str(path)


class Harness:
    def __init__(self,tmp_path):
        identity_db,work_db=tmp_path/"identity.db",tmp_path/"work.db"
        ids=IdentityStore(identity_db);ids.initialize(owner_display_name="Jaco");self.owner=ids.current_owner()
        self.policy_store=PolicyStore(identity_db);self.policy_store.initialize();policy=OwnerPolicy(self.policy_store)
        self.action_store=ActionStore(work_db);self.action_store.initialize();evidence=EvidenceStore(work_db);evidence.initialize()
        self.registry=CapabilityRegistry();self.actions=ActionRuntime(policy=policy,store=self.action_store,evidence=evidence,executor_resolver=self.registry.executor)
        self.capabilities=CapabilityRuntime(self.registry,self.actions,policy)
        self.artifacts=ArtifactStore(work_db);self.artifacts.initialize()
        self.root=tmp_path/"watched";self.root.mkdir();roots=SourceRootStore(identity_db);roots.initialize();roots.put(root_id="manuals",host_path=str(self.root),display_name="Manuals")
        self.sources=SourceRuntime(roots,self.registry,self.artifacts);ArtifactRuntime(self.artifacts,self.registry,self.sources)
        provider=SubprocessRepresentationProvider(_provider_script(tmp_path));self.representations=RepresentationRuntime(self.artifacts,self.sources,self.registry,provider)
        ks=KnowledgeStore(work_db);ks.initialize();passages=PassageStore(work_db);passages.initialize();generations=GenerationStore(work_db);generations.initialize()
        self.indexing=IndexingRuntime(passages,generations,self.artifacts,self.sources);self.knowledge=KnowledgeRuntime(ks,self.registry,self.indexing)
        ws=WorkStore(work_db);ws.initialize();self.work=WorkRuntime(ws,self.capabilities,self.action_store);self.work_store=ws
        self.model=IntakeProvider();ins=ArtifactIntakeStore(work_db);ins.initialize();self.intake=ArtifactIntakeRuntime(ins,self.artifacts,self.model,self.work,self.registry,self.capabilities,representations=self.representations)
        for scope,op,decision in [("files/local/manuals","diff","YES"),("files/local/manuals","inspect","YES"),("files/local/manuals","derive","YES"),("files/local/manuals","extract_text","YES"),("atlas/artifacts/intake","classify","YES"),("atlas/knowledge/index","index","YES"),("atlas/knowledge/index","verify","YES"),("atlas/knowledge/index","activate","CONFIRM")]:
            self.policy_store.set(principal_id=self.owner.principal_id,scope=scope,operation=op,decision=decision)
    def invoke(self,cid,payload):return self.capabilities.invoke(cid,payload,provenance=InvocationProvenance(self.owner.principal_id,"human","control"))
    def artifact(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Service interval is 900 operating hours.")
        doc.save(self.root / "scan.pdf")
        doc.close()
        diff=self.invoke("artifacts.diff_source",{"root_id":"manuals"});assert diff.status=="succeeded",diff.error
        return diff.result["new"][0]["artifact_id"]


def test_subprocess_representation_provider_creates_grounded_derived_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_SECRET_SENTINEL", "must-not-cross-provider-boundary")
    h=Harness(tmp_path);artifact_id=h.artifact()
    result=h.invoke("representations.derive",{"artifact_id":artifact_id,"need":"ocr"})
    assert result.status=="succeeded",result.error
    derived=h.artifacts.get(result.result["representation_artifact_id"])
    assert derived["provenance"]["parents"]==[artifact_id]
    assert derived["provenance"]["relation"]=="derived_representation"
    assert derived["provenance"]["representation_need"]=="ocr"
    assert derived["provenance"]["provider_version"]=="test-ocr@1"
    assert derived["facets"][0]["relative_path"].startswith(".atlas-derived/representation-ocr-")


def test_knowledge_workflow_maps_semantic_text_need_to_runtime_extraction(tmp_path):
    h=Harness(tmp_path);artifact_id=h.artifact()
    routed=h.invoke("artifacts.classify_intake",{"artifact_id":artifact_id,"source_event_kind":"new"})
    assert routed.status=="succeeded",routed.error
    work=routed.result["work"];assert work and work["display_ref"]=="AA-001"
    assert json.loads(routed.result["intake"]["representation_needs_json"])==["text"]
    steps=h.work_store.steps(work["work_id"]);assert steps[0].capability_id=="files.extract_text"
    result=h.work.run(work["work_id"]);assert result["status"]=="waiting_confirmation"
    occurrence_id=result["steps"][3]["occurrence_id"]
    confirmed=h.actions.confirm(occurrence_id,principal_id=h.owner.principal_id);assert confirmed.status=="succeeded"
    completed=h.work.run(work["work_id"]);assert completed["status"]=="completed"
    rows=h.knowledge.retrieve("900 operating hours",limit=5,filters={"artifact_id":artifact_id})
    assert rows and "900 operating hours" in rows[0]["content"]
    assert rows[0]["grounding"]["artifact_id"]==artifact_id


def test_representation_derivation_obeys_source_policy(tmp_path):
    h=Harness(tmp_path);artifact_id=h.artifact()
    h.policy_store.set(principal_id=h.owner.principal_id,scope="files/local/manuals",operation="derive",decision="NO")
    result=h.invoke("representations.derive",{"artifact_id":artifact_id,"need":"ocr"})
    assert result.status=="blocked" and result.policy_decision=="NO"
    assert len(h.artifacts.list(h.owner.principal_id))==1


def test_subprocess_provider_advertises_only_configured_representation_needs(tmp_path):
    provider=SubprocessRepresentationProvider(_provider_script(tmp_path), supported_needs=["ocr"])
    assert provider.available("ocr")[0] is True
    ok, reason=provider.available("layout")
    assert ok is False and "does not advertise layout" in reason
