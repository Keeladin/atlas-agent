from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shutil
import statistics
import tempfile
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.artifacts import ArtifactRuntime, ArtifactStore
from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.evidence import EvidenceStore
from atlas_core.identity import IdentityStore
from atlas_core.knowledge import KnowledgeRuntime, KnowledgeStore
from atlas_core.knowledge.generations import GenerationStore
from atlas_core.knowledge.indexing import IndexingRuntime
from atlas_core.knowledge.passages import PassageStore, segment
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.provenance import InvocationProvenance
from atlas_core.representations import RepresentationRuntime
from atlas_core.sources import SourceRootStore, SourceRuntime

SCAN_CHAR_THRESHOLD = 20
ROOT_ID = "manuals"
class DiagnosticRuntime:
    def __init__(self, instance_root: Path, source_root: Path) -> None:
        identity_db = instance_root / "identity.db"
        work_db = instance_root / "work.db"
        self.identities = IdentityStore(identity_db)
        self.identities.initialize(owner_display_name="Diagnostic")
        self.owner = self.identities.current_owner()
        self.policy_store = PolicyStore(identity_db); self.policy_store.initialize()
        policy = OwnerPolicy(self.policy_store)
        self.actions_store = ActionStore(work_db); self.actions_store.initialize()
        evidence = EvidenceStore(work_db); evidence.initialize()
        self.registry = CapabilityRegistry()
        self.actions = ActionRuntime(policy=policy, store=self.actions_store, evidence=evidence,
                                     executor_resolver=self.registry.executor)
        self.capabilities = CapabilityRuntime(self.registry, self.actions, policy)
        self.artifacts = ArtifactStore(work_db); self.artifacts.initialize()
        self.roots = SourceRootStore(identity_db); self.roots.initialize()
        self.roots.put(root_id=ROOT_ID, host_path=str(source_root), display_name="Diagnostic manual")
        self.sources = SourceRuntime(self.roots, self.registry, self.artifacts)
        ArtifactRuntime(self.artifacts, self.registry, self.sources)
        self.representations = RepresentationRuntime(self.artifacts, self.sources, self.registry)
        ks = KnowledgeStore(work_db); ks.initialize()
        self.passages = PassageStore(work_db); self.passages.initialize()
        self.generations = GenerationStore(work_db); self.generations.initialize()
        self.indexing = IndexingRuntime(self.passages, self.generations, self.artifacts, self.sources)
        self.knowledge = KnowledgeRuntime(ks, self.registry, self.indexing)
        for operation in ("diff", "inspect", "extract_text", "derive"):
            self.policy_store.set(principal_id=self.owner.principal_id, scope="files/local/manuals",
                                  operation=operation, decision="YES")
        for operation in ("index", "verify", "activate"):
            self.policy_store.set(principal_id=self.owner.principal_id, scope="atlas/knowledge/index",
                                  operation=operation, decision="YES")

    def invoke(self, capability_id: str, payload: dict[str, Any]):
        return self.capabilities.invoke(capability_id, payload,
            provenance=InvocationProvenance(self.owner.principal_id, "human", "diagnostic"))


def _read_derived(runtime: DiagnosticRuntime, relative_path: str) -> str:
    root = runtime.roots.get(ROOT_ID)
    return runtime.sources.kernel.read(root.provider_namespace, root.root_id, relative_path,
                                       configuration_revision=runtime.sources._revision(root)).text
def _parse_pdf_pages(text: str, total_pages: int) -> tuple[list[int], list[int]]:
    parts = re.split(r"(?m)^# Page (\d+)\s*$", text)
    chars = [0] * total_pages
    table_pages: set[int] = set()
    for index in range(1, len(parts), 2):
        page = int(parts[index]); body = parts[index + 1] if index + 1 < len(parts) else ""
        if 1 <= page <= total_pages:
            chars[page - 1] = len(re.sub(r"\s+", "", body))
        for found in re.findall(r"(?m)^## Table (\d+)\.", body):
            table_pages.add(int(found))
    return chars, sorted(table_pages)


def _parse_ocr_pages(text: str, total_pages: int) -> list[int]:
    parts = re.split(r"(?m)^--- page (\d+) ---\s*$", text, flags=re.I)
    chars = [0] * total_pages
    for index in range(1, len(parts), 2):
        page = int(parts[index]); body = parts[index + 1] if index + 1 < len(parts) else ""
        if 1 <= page <= total_pages:
            chars[page - 1] = len(re.sub(r"\s+", "", body))
    if not any(chars) and total_pages == 1:
        chars[0] = len(re.sub(r"\s+", "", text))
    return chars


def _structural_pdf_scan(path: Path) -> dict[str, Any]:
    import pymupdf
    doc = pymupdf.open(path)
    image_pages: list[int] = []; drawing_pages: list[int] = []; detected_table_pages: list[int] = []
    try:
        for number, page in enumerate(doc, 1):
            if page.get_images(full=True): image_pages.append(number)
            try:
                if page.get_drawings(): drawing_pages.append(number)
            except Exception:
                pass
            try:
                tables = page.find_tables()
                if getattr(tables, "tables", ()):
                    detected_table_pages.append(number)
            except Exception:
                pass
        return {"pages": len(doc), "image_pages": image_pages, "drawing_pages": drawing_pages,
                "detected_table_pages": detected_table_pages}
    finally:
        doc.close()


def _segmentation_report(text: str) -> dict[str, Any]:
    rows = segment(text)
    sizes = [len(row["content"]) for row in rows]
    headings = sum(1 for row in rows if row["locator"].get("heading_path"))
    return {
        "passages": len(rows), "headings_found": headings,
        "chars": sum(sizes), "size_min": min(sizes) if sizes else 0,
        "size_median": int(statistics.median(sizes)) if sizes else 0,
        "size_max": max(sizes) if sizes else 0,
    }


def _load_questions(path: Path | None) -> list[dict[str, str | None]]:
    if path is None: return []
    rows: list[dict[str, str | None]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"): continue
        question, sep, expected = line.partition("\t")
        rows.append({"question": question.strip(), "expected": expected.strip() if sep and expected.strip() else None})
    return rows
def diagnose_manual(pdf_path: Path, *, questions_path: Path | None = None,
                    scratch_root: Path | None = None) -> dict[str, Any]:
    source = pdf_path.resolve()
    if not source.is_file(): raise FileNotFoundError(source)
    owned_temp = tempfile.TemporaryDirectory(prefix="atlas-manual-diagnostic-") if scratch_root is None else None
    scratch = Path(owned_temp.name) if owned_temp is not None else scratch_root.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    instance_root = scratch / "instance"; source_root = scratch / "source"
    instance_root.mkdir(exist_ok=True); source_root.mkdir(exist_ok=True)
    copied = source_root / source.name
    shutil.copy2(source, copied)
    runtime = DiagnosticRuntime(instance_root, source_root)
    structural = _structural_pdf_scan(copied)
    total_pages = structural["pages"]
    report: dict[str, Any] = {
        "source": str(source), "scratch_root": str(scratch), "source_copy": str(copied),
        "production_touched": False, "structural": structural,
    }
    diff = runtime.invoke("artifacts.diff_source", {"root_id": ROOT_ID})
    if diff.status != "succeeded": raise RuntimeError(diff.error or diff.error_code or "source diff failed")
    artifact_id = diff.result["new"][0]["artifact_id"]
    report["artifact_id"] = artifact_id

    inspected = runtime.invoke("artifacts.inspect", {"artifact_id": artifact_id})
    report["inspection"] = inspected.result["inspection"] if inspected.status == "succeeded" else {
        "status": inspected.status, "error": inspected.error or inspected.error_code}
    extracted_text = ""
    extraction_artifact_id = None
    extraction = runtime.invoke("files.extract_text", {"root_id": ROOT_ID, "relative_path": copied.name, "extractor": "pdf@1"})
    if extraction.status == "succeeded":
        extracted_text = _read_derived(runtime, extraction.result["derived_relative_path"])
        extraction_artifact_id = extraction.result["extraction_artifact_id"]
        chars_per_page, table_pages = _parse_pdf_pages(extracted_text, total_pages)
        scanned_pages = [i + 1 for i, chars in enumerate(chars_per_page) if chars < SCAN_CHAR_THRESHOLD]
        report["text_extraction"] = {
            "status": "succeeded", "chars": len(extracted_text), "chars_per_page": chars_per_page,
            "scanned_pages": scanned_pages, "scanned_fraction": (len(scanned_pages) / total_pages) if total_pages else 0.0,
            "table_pages": table_pages, "extractor": extraction.result.get("extractor_config_id"),
        }
    else:
        report["text_extraction"] = {"status": extraction.status, "error": extraction.error or extraction.error_code,
                                     "chars": 0, "chars_per_page": None,
                                     "scanned_pages": None, "scanned_fraction": None, "table_pages": None}

    ocr_ok, ocr_reason = runtime.representations.available("ocr")
    ocr_text = ""; ocr_artifact_id = None
    if ocr_ok:
        try:
            ocr = runtime.invoke("representations.derive", {"artifact_id": artifact_id, "need": "ocr"})
        except RuntimeError as exc:
            report["ocr"] = {"status": "unavailable", "reason": str(exc)}
        else:
            if ocr.status == "succeeded":
                ocr_artifact_id = ocr.result["representation_artifact_id"]
                derived = runtime.artifacts.get(ocr_artifact_id)
                facet = next(row for row in derived["facets"] if row["kind"] == "local_file")
                ocr_text = _read_derived(runtime, facet["relative_path"])
                provenance = derived.get("provenance") or {}
                ocr_chars = _parse_ocr_pages(ocr_text, total_pages)
                recovered = [i + 1 for i, chars in enumerate(ocr_chars) if chars >= SCAN_CHAR_THRESHOLD]
                report["ocr"] = {"status": "succeeded", "chars": len(ocr_text), "chars_per_page": ocr_chars,
                                 "pages_recovered": recovered, "provider_metadata": provenance.get("provider_metadata") or {},
                                 "provider_version": provenance.get("provider_version")}
            else:
                report["ocr"] = {"status": ocr.status, "reason": ocr.error or ocr.error_code}
    else:
        report["ocr"] = {"status": "unavailable", "reason": ocr_reason}
    report["segmentation"] = {
        "pdf_text": _segmentation_report(extracted_text),
        "ocr": _segmentation_report(ocr_text) if ocr_text else None,
    }
    retrieval_artifact = extraction_artifact_id
    retrieval_text_kind = "pdf@1"
    if float(report["text_extraction"].get("scanned_fraction") or 0.0) > 0 and ocr_artifact_id:
        retrieval_artifact = ocr_artifact_id; retrieval_text_kind = "ocr"
    retrieval_rows: list[dict[str, Any]] = []
    generation_id = None
    if retrieval_artifact:
        indexed = runtime.indexing.index(source_artifact_id=artifact_id,
                                         extraction_artifact_id=retrieval_artifact,
                                         occurrence_hint="diagnose_manual")
        generation_id = indexed["generation_id"]
        verification = runtime.indexing.verify(generation_id)
        if verification.get("ok"):
            runtime.indexing.activate(generation_id)
        for item in _load_questions(questions_path):
            hits = runtime.knowledge.retrieve(str(item["question"]), limit=5,
                                              filters={"artifact_id": artifact_id})
            scores = [float(hit["score"]) for hit in hits]
            expected = item["expected"]
            retrieval_rows.append({
                "question": item["question"], "expected": expected,
                "answer_present": None if not expected else any(str(expected).casefold() in hit["content"].casefold() for hit in hits),
                "score_spread": (max(scores) - min(scores)) if scores else None,
                "top_passages": [{"score": hit["score"], "content": hit["content"][:700],
                                  "grounding": hit["grounding"]} for hit in hits],
            })
    report["retrieval"] = {"representation": retrieval_text_kind if retrieval_artifact else None,
                           "generation_id": generation_id, "questions": retrieval_rows}
    pages = max(1, total_pages)
    scanned_value = report["text_extraction"].get("scanned_fraction")
    scanned_fraction = float(scanned_value) if scanned_value is not None else None
    visual_pages = sorted(set(structural["image_pages"]) | set(structural["drawing_pages"]))
    report["coverage_verdict"] = {
        "layout": {"unreachable_fraction": 1.0 if total_pages else 0.0,
                   "reason": "pdf@1 preserves text order and table rows, not page layout semantics."},
        "tables": {"unreachable_fraction": scanned_fraction,
                   "known_table_pages": structural["detected_table_pages"],
                   "reason": "Born-digital tables detected by PyMuPDF are serialized by pdf@1; image-only pages remain at risk for table semantics."},
        "visual": {"unreachable_fraction": len(visual_pages) / pages if total_pages else 0.0,
                   "visual_pages": visual_pages,
                   "reason": "Images and vector drawings are not semantically represented by pdf@1 or OCR."},
        "multi_representation_ingest": {
            "required": bool(extracted_text and ocr_text and scanned_fraction is not None and scanned_fraction > 0.0),
            "reason": "Current workflow selects one representation; mixed born-digital/scanned documents may need both."},
    }
    if owned_temp is not None:
        report["scratch_root_ephemeral"] = True
        owned_temp.cleanup()
    else:
        report["scratch_root_ephemeral"] = False
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Atlas manual coverage diagnostic")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--questions", type=Path, help="UTF-8 lines: question<TAB>expected answer text")
    parser.add_argument("--scratch-root", type=Path, help="Optional scratch destination; defaults to a temporary directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    args = parser.parse_args()
    if args.json:
        with contextlib.redirect_stdout(io.StringIO()):
            report = diagnose_manual(args.pdf, questions_path=args.questions, scratch_root=args.scratch_root)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return
    report = diagnose_manual(args.pdf, questions_path=args.questions, scratch_root=args.scratch_root)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
