# P4 — Representation Provider Boundary

> **Current status (2026-09-03):** Implemented historical slice with one terminology change superseded by P5: `ocr` is no longer a model-facing semantic representation need. Current semantic needs are `text`, `layout`, `tables`, and `visual`; OCR is a runtime extraction mechanism used when native searchable text is unavailable.

## Responsibility

`knowledge.ingest` may require semantic representations that deterministic local extractors cannot produce. Atlas keeps that heavy interpretation behind a replaceable representation-provider boundary rather than embedding OCR or vision models into Source acquisition.

At the P4 point in history the classifier could declare `ocr`, `layout`, `tables`, or `visual`. P5 superseded that contract: the current model-facing semantic `representation_needs` are `text`, `layout`, `tables`, and `visual`. OCR is now a runtime mechanism for satisfying searchable text when native text is unavailable. The model still never names an implementation, process, model, provider or capability; runtime maps validated semantic needs to registered workflow mechanics.

```text
Artifact inspection
→ semantic classification + representation needs
→ runtime-owned knowledge.ingest template
→ deterministic extractor OR representation provider
→ derived Artifact with lineage
→ Knowledge index → verify → CONFIRM activation
```

## First executable need: OCR

`ocr` is the first provider-backed representation. `representations.derive` is source-scoped and crosses ordinary policy using operation `derive`. Complete source bytes are acquired through the retained-root/openat Source boundary and remain subject to the 64 MiB extraction cap.

The default provider is an external subprocess configured by `ATLAS_REPRESENTATION_PROVIDER_COMMAND`. Source bytes are supplied on stdin; the process receives only explicit representation request metadata and a minimal execution environment. Atlas/model credentials are not inherited.
Provider output is bounded JSON containing text plus provider metadata/version. Runtime materializes the text in the managed `.atlas-derived` area and registers a new Artifact with `relation: derived_representation`, the source Artifact as parent, the representation need, provider identity/version and source byte hash.

If OCR is requested but no provider is configured or available, Atlas records `workflow_unavailable_for_artifact`; it does not silently fall back to a misleading text-only ingestion.

In the original P4 slice, `layout`, `tables`, and `visual` were deliberately unavailable and failed closed. P5 later made those semantic needs executable through the combined governed interpretation path; this paragraph is retained only as the P4 implementation-history boundary.

## Invariant

Workflow completion means the representation contract selected for that Work was fulfilled. It does not assert that every modality in the source Artifact has been understood. New representation providers can add derived Artifacts later without changing Artifact identity or rewriting historical Work/evidence.
## Live OCR backend

The first real provider implementation is `atlas_providers/representation_ocr.py`, backed by RapidOCR + ONNX Runtime and PyMuPDF. It supports `ocr` only; provider availability now advertises configured semantic needs so a live OCR command cannot accidentally make `layout`, `tables` or `visual` appear executable.

The OCR engine lives in a separate external virtual environment under `atlas-agent-state/representation-ocr`, not in the Atlas runtime environment. `scripts/setup_representation_ocr.sh` builds that environment and replaces RapidOCR's desktop OpenCV wheel with the headless build required by the server.

For PDFs, the provider rasterizes each page and OCRs the raster, emitting page markers plus ordered recognized lines. For image artifacts it OCRs the supplied image directly. Output metadata records page count, recognized-line count, confidence and provider version.

A live scanned-PDF smoke proved the full path: raster-only PDF → `representations.derive` → OCR derived Artifact → `knowledge.index` → verification → CONFIRM activation → grounded retrieval of OCR-only facts from the original source Artifact.

Production activation still requires configuring `ATLAS_REPRESENTATION_PROVIDER_COMMAND` to the external provider Python plus the provider script; this implementation does not silently alter the running service configuration.
