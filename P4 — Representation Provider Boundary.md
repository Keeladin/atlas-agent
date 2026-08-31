# P4 — Representation Provider Boundary

## Responsibility

`knowledge.ingest` may require semantic representations that deterministic local extractors cannot produce. Atlas keeps that heavy interpretation behind a replaceable representation-provider boundary rather than embedding OCR or vision models into Source acquisition.

The model may declare semantic `representation_needs` (`ocr`, `layout`, `tables`, `visual`). It never names an implementation, process, model, provider or capability. Runtime validates the need and maps only supported needs to registered workflow mechanics.

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

Other declared needs (`layout`, `tables`, `visual`) are deliberately not executable in this slice. A workflow that requests them fails closed as unavailable instead of ignoring the need.

## Invariant

Workflow completion means the representation contract selected for that Work was fulfilled. It does not assert that every modality in the source Artifact has been understood. New representation providers can add derived Artifacts later without changing Artifact identity or rewriting historical Work/evidence.
## Live OCR backend

The first real provider implementation is `atlas_providers/representation_ocr.py`, backed by RapidOCR + ONNX Runtime and PyMuPDF. It supports `ocr` only; provider availability now advertises configured semantic needs so a live OCR command cannot accidentally make `layout`, `tables` or `visual` appear executable.

The OCR engine lives in a separate external virtual environment under `atlas-agent-state/representation-ocr`, not in the Atlas runtime environment. `scripts/setup_representation_ocr.sh` builds that environment and replaces RapidOCR's desktop OpenCV wheel with the headless build required by the server.

For PDFs, the provider rasterizes each page and OCRs the raster, emitting page markers plus ordered recognized lines. For image artifacts it OCRs the supplied image directly. Output metadata records page count, recognized-line count, confidence and provider version.

A live scanned-PDF smoke proved the full path: raster-only PDF → `representations.derive` → OCR derived Artifact → `knowledge.index` → verification → CONFIRM activation → grounded retrieval of OCR-only facts from the original source Artifact.

Production activation still requires configuring `ATLAS_REPRESENTATION_PROVIDER_COMMAND` to the external provider Python plus the provider script; this implementation does not silently alter the running service configuration.
