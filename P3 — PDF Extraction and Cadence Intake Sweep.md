# P3 — PDF Extraction and Cadence Intake Sweep

## Outcome

P3 closes the first real extraction gap without changing the Artifact → classification → Work boundary.

`pdf@1` is a deterministic derived-text extractor implemented with PyMuPDF. It receives complete source bytes through the same retained-root/openat containment used by local Sources, fails closed above 64 MiB, and records extraction identity as `extractor:pdf@1`. The extractor emits page markers, reading-order text and detected tables as pipe-delimited rows. It deliberately does no OCR or image/diagram interpretation.

Workflow completion is scoped to the declared representation contract. A PDF can therefore complete `knowledge.ingest` through its `pdf@1` text/table representation while inspection continues to record unresolved visual/layout content. Completion never asserts that every modality in the Artifact has been understood.

## Monitored intake fan-out

Cadence now supports two kinds:

- `work_template` — the existing behavior: materialize ordinary Work when due.
- `intake_sweep` — diff one monitored Source and fan candidates into independent Artifact intake classifications outside Work.

The intake sweep crosses the ordinary `artifacts.diff_source` and `artifacts.classify_intake` capability gates. Each successful classification may create one ordinary Work item; the sweep itself is never Work and no Work step creates Work.

Detected events are durably queued before `max_candidates` is applied. This matters because `diff_source` establishes Artifact identities for all observed new files: without the queue, capped candidates would disappear from later diffs before they had been classified. Event fingerprints make unchanged re-sweeps idempotent while failed classifications remain pending for retry.

## Boundaries retained

No vector/retrieval redesign, workflow-version subsystem, branching Work engine, OCR, or in-process ML document-understanding stack was introduced. Existing extractor configuration, Artifact provenance, generation identity and `inspection.unresolved` remain the data from which later enrichment/coverage reporting can be derived.
