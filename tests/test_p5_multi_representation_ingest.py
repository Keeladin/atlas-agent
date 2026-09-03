from __future__ import annotations

import json

import fitz

from atlas_core.providers import ModelResponse
from tests.test_p4_representation_provider import Harness


class MultiRepresentationModel:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if request.capability_id == "artifact.intake":
            return ModelResponse(json.dumps({
                "artifact_class": "A", "purpose": "durable technical manual",
                "knowledge_disposition": "ingest", "relationship": "new", "creates_work": True,
                "workflow_class": "A", "workflow_intent": "knowledge.ingest", "confidence": 0.99,
                "inspection_sufficiency": "partial", "unresolved_questions": ["visual relationships require interpretation"],
                "representation_needs": ["text", "visual"], "reason": "Text and visual semantics are both durable knowledge.",
            }), "stub", "stub-model", {})
        assert request.capability_id == "representations.interpret"
        assert request.content and request.content[0].kind == "document"
        return ModelResponse(
            "# Visual interpretation\n\nPage 1: hydraulic manifold feeds the service actuator through the directional valve.",
            "stub", "stub-vision", {},
        )


def test_text_and_visual_share_one_generation(tmp_path):
    h = Harness(tmp_path)
    model = MultiRepresentationModel()
    h.model = model
    h.intake.providers = model
    h.representations.model_provider = model
    h.policy_store.set(principal_id=h.owner.principal_id, scope="files/local/manuals", operation="interpret", decision="YES")
    artifact_id = h.artifact()

    routed = h.invoke("artifacts.classify_intake", {"artifact_id": artifact_id, "source_event_kind": "new"})
    assert routed.status == "succeeded", routed.error
    work = routed.result["work"]
    assert work is not None

    steps = h.work_store.steps(work["work_id"])
    assert [step.capability_id for step in steps] == [
        "files.extract_text", "knowledge.index", "representations.interpret",
        "knowledge.index", "knowledge.verify_generation", "knowledge.activate_generation",
    ]

    running = h.work.run(work["work_id"])
    assert running["status"] == "completed"
    finished_steps = h.work_store.steps(work["work_id"])
    first_generation = finished_steps[1].output["generation_id"]
    second_generation = finished_steps[3].output["generation_id"]
    assert first_generation == second_generation
    verification = finished_steps[4].output["verification"]
    assert verification["required_extractions"]["checked"] == 2
    assert verification["required_extractions"]["missing"] == []

    visual_artifact = h.artifacts.get(finished_steps[2].output["representation_artifact_id"])
    assert visual_artifact["provenance"]["relation"] == "model_interpretation"
    assert visual_artifact["provenance"]["representation_needs"] == ["visual"]
    assert visual_artifact["provenance"]["reproducible"] is False

    assert finished_steps[5].status == "completed"
    completed = h.work.detail(work["work_id"])
    assert completed["status"] == "completed"

    hits = h.knowledge.retrieve("hydraulic manifold directional valve", limit=5, filters={"artifact_id": artifact_id})
    assert hits
    assert any("hydraulic manifold" in row["content"] for row in hits)
    assert all(row["grounding"]["generation_id"] == first_generation for row in hits)

    semantic_requests = [request for request in model.requests if request.capability_id == "representations.interpret"]
    assert len(semantic_requests) == 1



def _multi_page_pdf_artifact(h, *, pages: int = 6):
    doc = fitz.open()
    for page_no in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"Hydraulic manifold page {page_no}; directional valve service note.")
        page.draw_rect(fitz.Rect(72, 100, 420, 300))
        page.insert_text((90, 140), f"Diagram {page_no}")
    path = h.root / "large-manual.pdf"
    doc.save(path)
    doc.close()
    diff = h.invoke("artifacts.diff_source", {"root_id": "manuals"})
    assert diff.status == "succeeded", diff.error
    return next(row["artifact_id"] for row in diff.result["new"] if row["relative_path"] == "large-manual.pdf")


def test_oversized_pdf_is_interpreted_in_bounded_original_page_batches(tmp_path, monkeypatch):
    from atlas_core.representations import runtime as representation_runtime

    monkeypatch.setattr(representation_runtime, "MAX_MODEL_DOCUMENT_BYTES", 1)
    monkeypatch.setattr(representation_runtime, "MAX_MODEL_BATCH_BYTES", 32 * 1024)
    monkeypatch.setattr(representation_runtime, "MAX_MODEL_BATCH_PAGES", 2)
    h = Harness(tmp_path)
    model = MultiRepresentationModel()
    h.intake.providers = model
    h.representations.model_provider = model
    h.policy_store.set(principal_id=h.owner.principal_id, scope="files/local/manuals", operation="interpret", decision="YES")
    artifact_id = _multi_page_pdf_artifact(h, pages=6)

    routed = h.invoke("artifacts.classify_intake", {"artifact_id": artifact_id, "source_event_kind": "new"})
    assert routed.status == "succeeded", routed.error
    assert routed.result["workflow_preflight"]["ok"] is True
    semantic_plan = routed.result["workflow_preflight"]["semantic_interpretation"]
    assert semantic_plan["mode"] == "page_batches"
    assert semantic_plan["page_count"] == 6
    assert semantic_plan["batch_count"] == 3

    running = h.work.run(routed.result["work"]["work_id"])
    assert running["status"] == "completed"
    semantic_requests = [request for request in model.requests if request.capability_id == "representations.interpret"]
    assert len(semantic_requests) == 3
    assert [(r.metadata["page_start"], r.metadata["page_end"]) for r in semantic_requests] == [(1, 2), (3, 4), (5, 6)]
    assert all(r.content and r.content[0].kind == "document" for r in semantic_requests)

    semantic_step = h.work_store.steps(routed.result["work"]["work_id"])[2]
    assert semantic_step.output["interpretation_mode"] == "page_batches"
    assert semantic_step.output["batch_count"] == 3
    derived = h.artifacts.get(semantic_step.output["representation_artifact_id"])
    assert derived["provenance"]["batch_count"] == 3
    assert derived["provenance"]["page_count"] == 6


def test_semantic_preflight_rejects_unreadable_pdf_before_work_creation(tmp_path):
    h = Harness(tmp_path)
    model = MultiRepresentationModel()
    h.intake.providers = model
    h.representations.model_provider = model
    h.policy_store.set(principal_id=h.owner.principal_id, scope="files/local/manuals", operation="interpret", decision="YES")
    (h.root / "broken.pdf").write_bytes(b"%PDF-1.7\nthis is not a valid PDF structure\n%%EOF")
    diff = h.invoke("artifacts.diff_source", {"root_id": "manuals"})
    assert diff.status == "succeeded", diff.error
    artifact_id = next(row["artifact_id"] for row in diff.result["new"] if row["relative_path"] == "broken.pdf")

    routed = h.invoke("artifacts.classify_intake", {"artifact_id": artifact_id, "source_event_kind": "new"})
    assert routed.status == "succeeded", routed.error
    assert routed.result["work"] is None
    assert routed.result["intake"]["status"] == "workflow_unavailable_for_artifact"
    assert routed.result["workflow_preflight"]["ok"] is False
    assert "semantic preflight failed" in routed.result["workflow_preflight"]["reason"]
    assert h.work_store.list() == ()


class FailFirstSemanticModel(MultiRepresentationModel):
    def __init__(self):
        super().__init__()
        self.fail_next_semantic = True

    def generate(self, request):
        if request.capability_id == "representations.interpret" and self.fail_next_semantic:
            self.fail_next_semantic = False
            self.requests.append(request)
            raise RuntimeError("temporary model provider outage")
        return super().generate(request)


def _named_pdf_artifact(h, name: str, text: str):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(h.root / name)
    doc.close()
    diff = h.invoke("artifacts.diff_source", {"root_id": "manuals"})
    assert diff.status == "succeeded", diff.error
    return next(row["artifact_id"] for row in diff.result["new"] if row["relative_path"] == name)


def test_inflight_knowledge_ingest_owns_generation_until_activation(tmp_path):
    h = Harness(tmp_path)
    model = FailFirstSemanticModel()
    h.model = model
    h.intake.providers = model
    h.representations.model_provider = model
    h.policy_store.set(principal_id=h.owner.principal_id, scope="files/local/manuals", operation="interpret", decision="YES")

    first_artifact = _named_pdf_artifact(h, "first.pdf", "First manual hydraulic valve diagram.")
    first_route = h.invoke("artifacts.classify_intake", {"artifact_id": first_artifact, "source_event_kind": "new"})
    first_work_id = first_route.result["work"]["work_id"]
    first_paused = h.work.run(first_work_id)
    assert first_paused["status"] == "paused"
    assert first_paused["steps"][2]["status"] == "waiting"
    first_generation = first_paused["steps"][1]["output"]["generation_id"]
    assert h.indexing.generations.get(first_generation)["state"] == "building"
    assert h.indexing.generations.get(first_generation)["build_owner_work_id"] == first_work_id

    second_artifact = _named_pdf_artifact(h, "second.pdf", "Second manual drilling pump diagram.")
    second_route = h.invoke("artifacts.classify_intake", {"artifact_id": second_artifact, "source_event_kind": "new"})
    second_work_id = second_route.result["work"]["work_id"]
    second_paused = h.work.run(second_work_id)
    assert second_paused["status"] == "paused"
    assert second_paused["steps"][1]["status"] == "waiting"
    assert "another knowledge ingest owns" in (second_paused["steps"][1]["error"] or "")
    assert h.indexing.generations.get(first_generation)["state"] == "building"

    first_completed = h.work.resume(first_work_id)
    assert first_completed["status"] == "completed"

    second_completed = h.work.resume(second_work_id)
    assert second_completed["status"] == "completed"
    second_generation = second_completed["steps"][1]["output"]["generation_id"]
    assert second_generation != first_generation
