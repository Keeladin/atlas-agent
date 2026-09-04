from __future__ import annotations

from atlas_api.compose import build_runtime
from atlas_core.provenance import InvocationProvenance
from tests.test_p3_pdf_workflow import _make_pdf


def _runtime_with_manual(tmp_path):
    rt=build_runtime(tmp_path/"instance");owner=rt.identities.current_owner().principal_id
    path=rt.instance_root/"library-clean"/"manual.pdf";_make_pdf(path)
    diff=rt.capabilities.invoke("artifacts.diff_source",{"root_id":"atlas-library-clean"},provenance=InvocationProvenance(owner,"human","control"))
    assert diff.status=="succeeded",diff.error
    artifact_id=next(row["artifact_id"] for row in diff.result["new"] if row["relative_path"]=="manual.pdf")
    return rt,owner,artifact_id


def test_explicit_knowledge_ingest_creates_and_runs_one_closed_pipeline_work(tmp_path):
    rt,owner,artifact_id=_runtime_with_manual(tmp_path)
    result=rt.capabilities.invoke("knowledge.ingest",{"artifact_id":artifact_id,"representation_needs":["text"]},provenance=InvocationProvenance(owner,"human","chat"))
    assert result.status=="succeeded",result.error
    work=result.result
    assert work["status"]=="completed"
    assert work["metadata"]["workflow_intent"]=="knowledge.ingest"
    assert [step["capability_id"] for step in work["steps"]]==["files.extract_text","knowledge.index","knowledge.verify_generation","knowledge.activate_generation"]
    assert all(step["capability_id"]!="knowledge.ingest" for step in work["steps"])
    assert len(rt.work_store.list())==1


def test_explicit_knowledge_ingest_is_state_aware_and_does_not_duplicate_completed_index(tmp_path):
    rt,owner,artifact_id=_runtime_with_manual(tmp_path)
    provenance=InvocationProvenance(owner,"human","chat")
    first=rt.capabilities.invoke("knowledge.ingest",{"artifact_id":artifact_id,"representation_needs":["text"]},provenance=provenance)
    assert first.status=="succeeded" and first.result["status"]=="completed"
    count=len(rt.work_store.list())
    second=rt.capabilities.invoke("knowledge.ingest",{"artifact_id":artifact_id,"representation_needs":["text"]},provenance=provenance)
    assert second.status=="succeeded",second.error
    assert second.result.get("already_indexed") is True
    assert len(rt.work_store.list())==count
