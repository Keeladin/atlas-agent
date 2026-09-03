from __future__ import annotations

import builtins
import json
import os
import sqlite3

import pymupdf

from atlas_core.artifacts.intake import MAX_INTAKE_ATTEMPTS, ArtifactIntakeStore
from atlas_core.cadence import CadenceRuntime, CadenceStore
from atlas_core.providers import ModelResponse
from atlas_core.sources.local import MAX_EXTRACTION_BYTES
from tests.test_p2_monitored_sources import Harness, StubProvider, decision


def _make_pdf(path, *, table: bool = False) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PX-417 Maintenance Manual")
    page.insert_text((72, 96), "Service interval is 137 operating hours.")
    if table:
        x0, y0, cell_w, cell_h = 72, 140, 140, 28
        for i in range(3):
            page.draw_line((x0 + i * cell_w, y0), (x0 + i * cell_w, y0 + 2 * cell_h))
        for j in range(3):
            page.draw_line((x0, y0 + j * cell_h), (x0 + 2 * cell_w, y0 + j * cell_h))
        page.insert_text((x0 + 8, y0 + 18), "Fastener")
        page.insert_text((x0 + cell_w + 8, y0 + 18), "Torque")
        page.insert_text((x0 + 8, y0 + cell_h + 18), "Retaining bolt")
        page.insert_text((x0 + cell_w + 8, y0 + cell_h + 18), "31 Nm")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Hydraulic brake inspection procedure")
    doc.save(path)
    doc.close()


def test_pdf_extractor_registers_versioned_extraction_and_preserves_tables(tmp_path):
    h = Harness(tmp_path)
    path = h.root / "manual.pdf"
    _make_pdf(path, table=True)
    artifact_id = h.scan()["new"][0]["artifact_id"]
    result = h.invoke("files.extract_text", {"root_id": "manuals", "relative_path": "manual.pdf"})
    assert result.status == "succeeded", result.error
    assert result.result["artifact_id"] == artifact_id
    assert result.result["extractor_config_id"] == "extractor:pdf@1"
    extraction = h.artifacts.get(result.result["extraction_artifact_id"])
    assert extraction["provenance"]["relation"] == "extracted_from"
    assert extraction["provenance"]["extractor_config_id"] == "extractor:pdf@1"
    derived = h.sources.kernel.read("local", "manuals", result.result["derived_relative_path"])
    assert "# Page 1" in derived.text and "# Page 2" in derived.text
    assert "| Fastener | Torque |" in derived.text
    assert "| Retaining bolt | 31 Nm |" in derived.text


def test_oversized_pdf_fails_closed_without_extraction_artifact(tmp_path):
    h = Harness(tmp_path)
    path = h.root / "oversized.pdf"
    with open(path, "wb") as handle:
        handle.write(b"%PDF-1.7\n")
        handle.truncate(MAX_EXTRACTION_BYTES + 1)
    source_id = h.scan()["new"][0]["artifact_id"]
    before = {row["artifact_id"] for row in h.artifacts.list(h.owner.principal_id)}
    result = h.invoke("files.extract_text", {"root_id": "manuals", "relative_path": "oversized.pdf"})
    assert result.status == "failed" and result.error_code == "too_large"
    after = {row["artifact_id"] for row in h.artifacts.list(h.owner.principal_id)}
    assert after == before == {source_id}


def test_missing_pymupdf_degrades_as_extractor_unavailable(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    path = h.root / "manual.pdf"
    _make_pdf(path)
    h.scan()
    original = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name == "pymupdf":
            raise ImportError("simulated missing wheel")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked)
    result = h.invoke("files.extract_text", {"root_id": "manuals", "relative_path": "manual.pdf"})
    assert result.status == "failed" and result.error_code == "extractor_unavailable"


def test_pdf_routes_end_to_end_and_retrieval_grounds_back_to_source(tmp_path):
    h = Harness(tmp_path)
    h.policy_store.set(principal_id=h.owner.principal_id, scope="atlas/knowledge", operation="retrieve", decision="YES")
    path = h.root / "manual.pdf"
    _make_pdf(path)
    artifact_id = h.scan()["new"][0]["artifact_id"]
    routed = h.invoke("artifacts.classify_intake", {"artifact_id": artifact_id, "source_event_kind": "new"}).result
    assert routed["intake"]["status"] == "routed"
    completed = h.work.run(routed["work"]["work_id"])
    assert completed["status"] == "completed"
    retrieved = h.invoke("knowledge.retrieve", {"need": "137 operating hours"})
    assert retrieved.status == "succeeded" and retrieved.result
    hit = next(row for row in retrieved.result if "137 operating hours" in row["content"])
    assert hit["grounding"]["artifact_id"] == artifact_id
    assert hit["grounding"]["extraction_artifact_id"] == completed["steps"][0]["output"]["extraction_artifact_id"]


def test_intake_sweep_fans_out_caps_and_is_idempotent(tmp_path):
    h = Harness(tmp_path)
    for name in ("a.md", "b.md", "c.md"):
        (h.root / name).write_text(f"# {name}\nDurable manual\n")
    summary = h.intake.sweep("manuals", h.owner.principal_id, max_candidates=2)
    assert summary["candidates"] == 3 and summary["processed"] == 2
    assert summary["skipped_cap"] == 1 and summary["work_created"] == 2
    for work in h.work_store.list():
        assert all(step.capability_id not in {"work.create", "cadence.create"} for step in h.work_store.steps(work.work_id))
    again = h.intake.sweep("manuals", h.owner.principal_id, max_candidates=3)
    assert again["processed"] == 1
    third = h.intake.sweep("manuals", h.owner.principal_id, max_candidates=3)
    assert third["processed"] == 0 and third["work_created"] == 0


def test_cadence_intake_sweep_records_summary_without_creating_wrapper_work(tmp_path):
    h = Harness(tmp_path)
    (h.root / "manual.md").write_text("# Manual\nDurable reference\n")
    store = CadenceStore(tmp_path / "cadence.db"); store.initialize()
    runtime = CadenceRuntime(store, h.work, h.intake)
    cadence = store.create(name="Watch manuals", objective="Monitor manual source", schedule={"kind":"interval","minutes":60},
                           steps=[], owner_principal_id=h.owner.principal_id, next_run_at="2000-01-01T00:00:00+00:00",
                           kind="intake_sweep", intake_root_id="manuals", max_candidates=25)
    tick = runtime.tick()
    assert len(tick) == 1 and tick[0]["kind"] == "intake_sweep"
    assert tick[0]["summary"]["work_created"] == 1
    saved = store.get(cadence.cadence_id)
    assert saved.last_result["processed"] == 1
    assert len(h.work_store.list()) == 1


class _FailingClassifier(StubProvider):
    """Returns model output that cannot satisfy CLASSIFICATION_SCHEMA for named artifacts."""

    def __init__(self, failing: set[str]) -> None:
        super().__init__(); self.failing = failing

    def generate(self, request):
        self.requests.append(request)
        payload = json.loads(request.input)
        name = str((payload.get("artifact") or {}).get("display_name") or "")
        body = {"unusable": True} if name in self.failing else decision()
        return ModelResponse(json.dumps(body), "stub", "stub-model", {})


def _age_pending(store, fingerprints, stamp="2000-01-01 00:00:00"):
    """Make queued events unambiguously oldest, reproducing the starvation ordering."""
    with store._db() as db:
        for fingerprint in fingerprints:
            db.execute("UPDATE artifact_intake_pending SET created_at=? WHERE event_fingerprint=?", (stamp, fingerprint))


def test_repeated_intake_failure_dead_letters_and_stops_calling_the_model(tmp_path):
    h = Harness(tmp_path)
    (h.root / "broken.md").write_text("# Broken\nUnclassifiable\n")
    h.intake.providers = _FailingClassifier({"broken.md"})

    sweeps = [h.intake.sweep("manuals", h.owner.principal_id, max_candidates=5) for _ in range(MAX_INTAKE_ATTEMPTS)]
    assert [s["failed"] for s in sweeps] == [1] * MAX_INTAKE_ATTEMPTS
    assert [s["results"][0]["attempts"] for s in sweeps] == [1, 2, 3]
    assert [s["results"][0]["queue_state"] for s in sweeps] == ["pending", "pending", "dead_letter"]
    assert [s["dead_lettered"] for s in sweeps] == [0, 0, 1]
    assert sweeps[-1]["dead_letter_events"][0]["attempts"] == MAX_INTAKE_ATTEMPTS
    assert sweeps[-1]["dead_letter_events"][0]["last_error"]

    assert h.intake_store.pending_events(h.owner.principal_id) == ()
    dead = h.intake_store.dead_letter_events(h.owner.principal_id)
    assert len(dead) == 1 and dead[0]["state"] == "dead_letter" and dead[0]["attempts"] == MAX_INTAKE_ATTEMPTS

    calls = len(h.intake.providers.requests)
    after = h.intake.sweep("manuals", h.owner.principal_id, max_candidates=5)
    assert after["candidates"] == 0 and after["failed"] == 0 and after["processed"] == 0
    assert len(h.intake.providers.requests) == calls
    assert h.work_store.list() == ()


def test_dead_lettering_releases_the_cap_for_healthy_candidates(tmp_path):
    h = Harness(tmp_path)
    for name in ("bad-1.md", "bad-2.md"):
        (h.root / name).write_text("# broken\n")
    h.intake.providers = _FailingClassifier({"bad-1.md", "bad-2.md"})

    first = h.intake.sweep("manuals", h.owner.principal_id, max_candidates=2)
    assert first["failed"] == 2 and first["work_created"] == 0
    _age_pending(h.intake_store, [row["event_fingerprint"] for row in h.intake_store.pending_events(h.owner.principal_id)])

    for name in ("good-1.md", "good-2.md"):
        (h.root / name).write_text("# healthy manual\nService interval 500 hours.\n")

    second = h.intake.sweep("manuals", h.owner.principal_id, max_candidates=2)
    assert second["candidates"] == 4 and second["failed"] == 2 and second["work_created"] == 0
    third = h.intake.sweep("manuals", h.owner.principal_id, max_candidates=2)
    assert third["failed"] == 2 and third["dead_lettered"] == 2 and third["work_created"] == 0

    fourth = h.intake.sweep("manuals", h.owner.principal_id, max_candidates=2)
    assert fourth["candidates"] == 2 and fourth["processed"] == 2 and fourth["work_created"] == 2
    dead = h.intake_store.dead_letter_events(h.owner.principal_id)
    assert len(dead) == 2 and all(row["attempts"] == MAX_INTAKE_ATTEMPTS for row in dead)
    assert {row["candidate"]["relative_path"] for row in dead} == {"bad-1.md", "bad-2.md"}
    assert h.intake_store.pending_events(h.owner.principal_id) == ()
    assert len(h.work_store.list()) == 2


def test_dead_lettered_event_is_not_resurrected_by_a_later_diff(tmp_path):
    h = Harness(tmp_path)
    (h.root / "broken.md").write_text("# Broken\n")
    h.intake.providers = _FailingClassifier({"broken.md"})
    for _ in range(MAX_INTAKE_ATTEMPTS):
        h.intake.sweep("manuals", h.owner.principal_id, max_candidates=5)
    dead = h.intake_store.dead_letter_events(h.owner.principal_id)[0]

    h.intake_store.enqueue_event(principal_id=h.owner.principal_id, artifact_id=dead["artifact_id"],
                                 source_event_kind=dead["source_event_kind"],
                                 event_fingerprint=dead["event_fingerprint"], candidate=dead["candidate"])
    assert h.intake_store.pending_events(h.owner.principal_id) == ()
    assert len(h.intake_store.dead_letter_events(h.owner.principal_id)) == 1


def test_requeue_restores_a_dead_lettered_event(tmp_path):
    h = Harness(tmp_path)
    (h.root / "broken.md").write_text("# Broken\n")
    h.intake.providers = _FailingClassifier({"broken.md"})
    for _ in range(MAX_INTAKE_ATTEMPTS):
        h.intake.sweep("manuals", h.owner.principal_id, max_candidates=5)
    dead = h.intake_store.dead_letter_events(h.owner.principal_id)[0]

    restored = h.intake_store.requeue_event(dead["event_fingerprint"])
    assert restored["state"] == "pending" and restored["attempts"] == 0 and restored["last_error"] is None
    assert h.intake_store.dead_letter_events(h.owner.principal_id) == ()

    h.intake.providers = h.provider
    summary = h.intake.sweep("manuals", h.owner.principal_id, max_candidates=5)
    assert summary["processed"] == 1 and summary["work_created"] == 1


def test_pending_queue_migrates_from_pre_attempt_schema(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE artifact_intake_pending(
            event_fingerprint TEXT PRIMARY KEY, principal_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
            source_event_kind TEXT NOT NULL, candidate_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("INSERT INTO artifact_intake_pending(event_fingerprint,principal_id,artifact_id,source_event_kind,candidate_json) VALUES ('fp','owner','artifact_1','new','{}')")

    store = ArtifactIntakeStore(path)
    store.initialize()
    rows = store.pending_events("owner")
    assert len(rows) == 1
    assert rows[0]["attempts"] == 0 and rows[0]["state"] == "pending"
    assert rows[0]["last_error"] is None and rows[0]["last_attempt_at"] is None
    assert store.dead_letter_events("owner") == ()
