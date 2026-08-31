from __future__ import annotations

import json

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
from atlas_core.sources import SourceRootStore, SourceRuntime
from atlas_core.work import WorkRuntime, WorkStore


def decision(*, artifact_class="A", workflow_class="A", workflow_intent="knowledge.ingest", creates_work=True):
    return {
        "artifact_class": artifact_class, "purpose": "durable technical reference",
        "knowledge_disposition": "ingest" if artifact_class == "A" else "retain",
        "relationship": "new", "creates_work": creates_work,
        "workflow_class": workflow_class if creates_work else None,
        "workflow_intent": workflow_intent if creates_work else None,
        "confidence": 0.94, "inspection_sufficiency": "sufficient", "unresolved_questions": [],
        "reason": "The artifact creates a durable responsibility.",
    }


class StubProvider:
    def __init__(self): self.requests=[]; self.responses=[]
    def queue(self, value): self.responses.append(value)
    def generate(self, request):
        self.requests.append(request)
        value = self.responses.pop(0) if self.responses else decision()
        return ModelResponse(json.dumps(value), "stub", "stub-model", {})


class Harness:
    def __init__(self, tmp_path):
        identity_db, work_db = tmp_path / "identity.db", tmp_path / "work.db"
        self.identities=IdentityStore(identity_db); self.identities.initialize(owner_display_name="Jaco"); self.owner=self.identities.current_owner()
        self.policy_store=PolicyStore(identity_db); self.policy_store.initialize(); policy=OwnerPolicy(self.policy_store)
        self.action_store=ActionStore(work_db); self.action_store.initialize(); evidence=EvidenceStore(work_db); evidence.initialize()
        self.registry=CapabilityRegistry(); self.actions=ActionRuntime(policy=policy,store=self.action_store,evidence=evidence,executor_resolver=self.registry.executor)
        self.capabilities=CapabilityRuntime(self.registry,self.actions,policy)
        self.artifacts=ArtifactStore(work_db); self.artifacts.initialize()
        self.root=tmp_path/"watched"; self.root.mkdir(); self.roots=SourceRootStore(identity_db); self.roots.initialize(); self.roots.put(root_id="manuals",host_path=str(self.root),display_name="Manuals")
        self.sources=SourceRuntime(self.roots,self.registry,self.artifacts); ArtifactRuntime(self.artifacts,self.registry,self.sources)
        self.knowledge_store=KnowledgeStore(work_db); self.knowledge_store.initialize(); self.passages=PassageStore(work_db); self.passages.initialize(); self.generations=GenerationStore(work_db); self.generations.initialize()
        self.indexing=IndexingRuntime(self.passages,self.generations,self.artifacts,self.sources); KnowledgeRuntime(self.knowledge_store,self.registry,self.indexing)
        self.work_store=WorkStore(work_db); self.work_store.initialize(); self.work=WorkRuntime(self.work_store,self.capabilities,self.action_store)
        self.provider=StubProvider(); self.intake_store=ArtifactIntakeStore(work_db); self.intake_store.initialize(); self.intake=ArtifactIntakeRuntime(self.intake_store,self.artifacts,self.provider,self.work,self.registry,self.capabilities)
        for scope,operation,answer in [
            ("files/local/manuals","diff","YES"),("files/local/manuals","inspect","YES"),("files/local/manuals","extract_text","YES"),
            ("atlas/artifacts/intake","classify","YES"),("atlas/knowledge/index","index","YES"),
            ("atlas/knowledge/index","verify","YES"),("atlas/knowledge/index","activate","CONFIRM")]:
            self.policy_store.set(principal_id=self.owner.principal_id,scope=scope,operation=operation,decision=answer)
    def invoke(self,cid,payload):
        return self.capabilities.invoke(cid,payload,provenance=InvocationProvenance(self.owner.principal_id,"human","control"))
    def scan(self):
        result=self.invoke("artifacts.diff_source",{"root_id":"manuals"}); assert result.status=="succeeded",result.error; return result.result


def test_diff_establishes_new_artifact_then_marks_changed_and_missing(tmp_path):
    h=Harness(tmp_path); path=h.root/"manual.md"; path.write_text("# Manual\nAlpha 123\n")
    first=h.scan(); assert first["counts"]=={"new":1,"changed":0,"missing":0}
    artifact_id=first["new"][0]["artifact_id"]; assert h.artifacts.get(artifact_id)["facets"][0]["state"]=="present"
    path.write_text("# Manual\nAlpha 124\n")
    changed=h.scan(); assert changed["changed"][0]["artifact_id"]==artifact_id; assert h.artifacts.find_local("manuals","manual.md")["state"]=="stale"
    path.unlink(); missing=h.scan(); assert missing["missing"][0]["artifact_id"]==artifact_id; assert h.artifacts.find_local("manuals","manual.md")["state"]=="missing"


def test_classification_happens_before_work_and_routes_to_human_reference(tmp_path):
    h=Harness(tmp_path); (h.root/"manual.md").write_text("# Manual\nService interval 500 hours.\n")
    artifact_id=h.scan()["new"][0]["artifact_id"]; assert h.work_store.list()==()
    classified=h.invoke("artifacts.classify_intake",{"artifact_id":artifact_id,"source_event_kind":"new"})
    assert classified.status=="succeeded",classified.error; result=classified.result
    assert result["intake"]["status"]=="routed"; assert result["work"]["display_ref"]=="AA-001"
    assert result["work"]["work_id"].startswith("work_"); assert result["work"]["status"]=="queued"
    sent=json.loads(h.provider.requests[-1].input); assert sent["artifact"]["artifact_id"]==artifact_id
    assert sent["inspection"]["format"]=="markdown"; assert sent["inspection"]["representations"][0]["kind"]=="text"
    assert result["intake"]["inspection_occurrence_id"]
    assert "available_workflows" in sent and "knowledge.ingest" in sent["available_workflows"]


def test_no_responsibility_means_no_work_id(tmp_path):
    h=Harness(tmp_path); (h.root/"receipt.txt").write_text("temporary export")
    artifact_id=h.scan()["new"][0]["artifact_id"]
    h.provider.queue(decision(artifact_class="D",creates_work=False,workflow_class=None,workflow_intent=None))
    result=h.invoke("artifacts.classify_intake",{"artifact_id":artifact_id,"source_event_kind":"new"}).result
    assert result["intake"]["status"]=="no_work"; assert result["work"] is None; assert h.work_store.list()==()


def test_unavailable_workflow_is_recorded_without_inventing_steps(tmp_path):
    h=Harness(tmp_path); (h.root/"shift-report.md").write_text("# Shift report")
    artifact_id=h.scan()["new"][0]["artifact_id"]
    h.provider.queue(decision(artifact_class="B",workflow_class="B",workflow_intent="operational.process"))
    result=h.invoke("artifacts.classify_intake",{"artifact_id":artifact_id,"source_event_kind":"new"}).result
    assert result["intake"]["status"]=="workflow_unavailable"; assert result["work"] is None; assert h.work_store.list()==()


def test_work_reference_numbers_are_per_route(tmp_path):
    h=Harness(tmp_path)
    a=h.work.create("A1",[{"capability_id":"artifacts.list","input":{}}],owner_principal_id=h.owner.principal_id,artifact_class="A",workflow_class="A")
    b=h.work.create("A2",[{"capability_id":"artifacts.list","input":{}}],owner_principal_id=h.owner.principal_id,artifact_class="A",workflow_class="A")
    c=h.work.create("E1",[{"capability_id":"artifacts.list","input":{}}],owner_principal_id=h.owner.principal_id,artifact_class="E",workflow_class="C")
    assert (a.display_ref,b.display_ref,c.display_ref)==("AA-001","AA-002","EC-001")
    assert len({a.work_id,b.work_id,c.work_id})==3


def test_routed_knowledge_work_runs_to_confirm_gated_activation(tmp_path):
    h=Harness(tmp_path); (h.root/"manual.md").write_text("# Manual\nService interval is 500 hours.\n")
    artifact_id=h.scan()["new"][0]["artifact_id"]
    routed=h.invoke("artifacts.classify_intake",{"artifact_id":artifact_id,"source_event_kind":"new"}).result["work"]
    result=h.work.run(routed["work_id"]); assert result["status"]=="waiting_confirmation"
    assert [s["status"] for s in result["steps"]]==["completed","completed","completed","waiting_confirmation"]
    pending=h.action_store.get(result["steps"][3]["occurrence_id"]); assert pending.policy_decision=="CONFIRM"


def test_diff_capability_obeys_root_policy(tmp_path):
    h=Harness(tmp_path); (h.root/"incoming.md").write_text("# Incoming\n")
    h.policy_store.set(principal_id=h.owner.principal_id,scope="files/local/manuals",operation="diff",decision="NO")
    occurrence=h.invoke("artifacts.diff_source",{"root_id":"manuals"}); assert occurrence.status=="blocked"; assert occurrence.policy_decision=="NO"; assert h.artifacts.list(h.owner.principal_id)==()


def test_work_reference_fails_closed_when_pointer_is_invalid(tmp_path):
    h=Harness(tmp_path); (h.root/"manual.md").write_text("# Manual\n")
    steps=[{"capability_id":"artifacts.diff_source","input":{"root_id":"manuals"}},
           {"capability_id":"knowledge.index","input":{"source_artifact_id":{"$ref":{"step":1,"output":"/nope"}},"extraction_artifact_id":"x"}}]
    work=h.work.create("Broken ref",steps,owner_principal_id=h.owner.principal_id); result=h.work.run(work.work_id)
    assert result["status"]=="failed"; assert "work reference path not found" in result["steps"][1]["error"]


def test_compound_docx_inspection_reports_text_tables_and_images_without_collapsing_modality(tmp_path):
    import zipfile
    h=Harness(tmp_path); path=h.root/"manual.docx"
    document='''<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>Hydraulic brake service manual</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Torque 42 Nm</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
    with zipfile.ZipFile(path,"w") as zf:
        zf.writestr("word/document.xml",document)
        zf.writestr("word/media/image1.png",b"\x89PNG\r\n\x1a\n"+b"0"*32)
    artifact_id=h.scan()["new"][0]["artifact_id"]
    inspected=h.invoke("artifacts.inspect",{"artifact_id":artifact_id})
    assert inspected.status=="succeeded",inspected.error
    view=inspected.result["inspection"]
    assert view["format"]=="docx" and view["compound"] is True
    kinds={row["kind"] for row in view["representations"]}
    assert {"text","table","embedded_image"} <= kinds
    assert "document_layout_and_visual_semantics" in view["unresolved"]


def test_compound_artifact_can_be_classified_but_is_not_sent_into_incomplete_text_only_workflow(tmp_path):
    import zipfile
    h=Harness(tmp_path); path=h.root/"manual.docx"
    with zipfile.ZipFile(path,"w") as zf:
        zf.writestr("word/document.xml",'<w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>Maintenance manual</w:t></w:r></w:p></w:body></w:document>')
        zf.writestr("word/media/image1.png",b"image")
    artifact_id=h.scan()["new"][0]["artifact_id"]
    result=h.invoke("artifacts.classify_intake",{"artifact_id":artifact_id,"source_event_kind":"new"}).result
    assert result["classification"]["workflow_intent"]=="knowledge.ingest"
    assert result["intake"]["status"]=="workflow_unavailable_for_artifact"
    assert result["work"] is None


def test_inspection_policy_no_blocks_classification_before_model_sees_content(tmp_path):
    h=Harness(tmp_path); (h.root/"manual.md").write_text("# Secret manual\nService interval 700 hours.\n")
    artifact_id=h.scan()["new"][0]["artifact_id"]
    h.policy_store.set(principal_id=h.owner.principal_id,scope="files/local/manuals/manual.md",operation="inspect",decision="NO")
    before=len(h.provider.requests)
    result=h.invoke("artifacts.classify_intake",{"artifact_id":artifact_id,"source_event_kind":"new"})
    assert result.status=="failed"
    assert "artifact inspection blocked" in (result.error or "")
    assert len(h.provider.requests)==before and h.work_store.list()==()


def test_pdf_inspection_is_explicitly_partial_instead_of_pretending_pdf_is_text(tmp_path):
    h=Harness(tmp_path)
    (h.root/"drawing.pdf").write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Page /Resources << /XObject << /Im0 << /Subtype /Image >> >> >> >> endobj\n%%EOF")
    artifact_id=h.scan()["new"][0]["artifact_id"]
    result=h.invoke("artifacts.inspect",{"artifact_id":artifact_id})
    assert result.status=="succeeded",result.error
    view=result.result["inspection"]
    assert view["format"]=="pdf" and view["compound"] is True
    assert any(row["kind"]=="embedded_image" for row in view["representations"])
    assert "document_text" in view["unresolved"]
    assert all("preview" not in row for row in view["representations"] if row["kind"]=="page_document")


def test_workflow_class_is_derived_deterministically_from_intent(tmp_path):
    h=Harness(tmp_path); (h.root/"manual.md").write_text("# Manual\nService interval 500 hours.\n")
    artifact_id=h.scan()["new"][0]["artifact_id"]
    routed=decision(); routed["workflow_class"]="not-a-runtime-code"; routed["representation_needs"]=["text"]
    h.provider.queue(routed)
    occurrence=h.invoke("artifacts.classify_intake",{"artifact_id":artifact_id,"source_event_kind":"new"})
    assert occurrence.status=="succeeded",occurrence.error
    assert occurrence.result["intake"]["status"]=="routed"
    assert occurrence.result["classification"]["workflow_class"]=="A"
    assert len(h.provider.requests)==1


def test_invalid_workflow_intent_gets_one_bounded_model_repair(tmp_path):
    h=Harness(tmp_path); (h.root/"manual.md").write_text("# Manual\nService interval 500 hours.\n")
    artifact_id=h.scan()["new"][0]["artifact_id"]
    bad=decision(workflow_class=None,workflow_intent="invented.workflow"); bad["representation_needs"]=["text"]
    fixed=decision(workflow_class=None); fixed["representation_needs"]=["text"]
    h.provider.queue(bad); h.provider.queue(fixed)
    occurrence=h.invoke("artifacts.classify_intake",{"artifact_id":artifact_id,"source_event_kind":"new"})
    assert occurrence.status=="succeeded",occurrence.error
    assert occurrence.result["classification"]["workflow_class"]=="A"
    assert len(h.provider.requests)==2
    assert h.provider.requests[1].capability_id=="artifact.intake.route_repair"
