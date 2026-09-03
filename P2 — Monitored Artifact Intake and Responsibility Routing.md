# P2 — Monitored Artifact Intake and Responsibility Routing

> **Current status (2026-09-03):** Implemented historical slice. Its Artifact → inspect → classify → runtime-owned workflow boundary remains current, but later P3/P4/P4.5/P5 work expanded extraction, managed custody and multi-representation ingest. Use `Atlas Architecture — Runtime and Topology.md` for present-tense runtime truth.

## Approved responsibility model

A monitored Source event is not Work. Detection establishes or resolves an Artifact, then Atlas performs one bounded semantic classification before any Work ID exists.

```text
Source event
→ Artifact established/resolved
→ semantic classification
→ workflow intent selected
→ Work created only when a durable responsibility exists
```

Classification answers what responsibility the Artifact creates, not how its file format should be processed. Artifact type is therefore not modality: one technical manual may contain text, tables, drawings, photographs, diagrams and scanned pages while remaining one Artifact.

Before classification, Atlas performs a separately governed bounded Artifact inspection. Inspection is structural rather than semantic: it reports observed representations, format/container structure and unresolved modalities without deciding purpose. The classifier therefore receives evidence such as text preview, package parts, tables, embedded images or an explicit `unresolved` list instead of treating the filename or extension as the document's meaning.

The classifier records: artifact class, semantic purpose, Knowledge disposition, relationship hint, whether Work is required, workflow class/intent, confidence, inspection sufficiency, unresolved questions and reason. It must not prescribe OCR, chunking, embeddings, image extraction, table parsing or other workflow mechanics.

## Human-readable Work references

The database `work_id` remains the immutable opaque identity. Routed Work additionally receives a display reference such as `AA-001`.

- first letter: Artifact/responsibility class
- second letter: workflow class
- number: sequence within that route

Examples: `AA-001`, `AA-002`, `EC-001`. Historical identity and links always use the opaque `work_id`, never the display reference.
## Initial class codes

Artifact/responsibility classes:

- `A` — durable reference / Knowledge-worthy material
- `B` — operational input
- `C` — evidence
- `D` — transactional or administrative record
- `E` — review / uncertain

Workflow classes:

- `A` — `knowledge.ingest`
- `B` — `operational.process`
- `C` — `owner.review`

The taxonomy is semantic configuration, not identity. The opaque IDs survive future taxonomy changes.

## Runtime boundary

The model selects only semantic class and workflow intent. Runtime validates that class and intent agree, then materializes only a registered workflow template. Model output never becomes arbitrary capability steps.

At this P2 slice, `knowledge.ingest` is the only executable workflow template. An unavailable workflow is recorded as `workflow_unavailable`; Atlas does not invent steps to make it executable.

`knowledge.ingest` currently projects to governed extraction → indexing → deterministic generation verification → CONFIRM-gated activation only when the inspection shows a representation the present pipeline can cover. A compound or unsupported artifact may still be correctly classified as `knowledge.ingest`, but intake records `workflow_unavailable_for_artifact` and creates no doomed Work. The workflow may later gain PDF, Office, vision, table or other representation handlers without changing intake classification.

Source diffing remains deterministic. New paths are established as Artifacts before classification; changed and missing facets are marked stale/missing. Classification and routing occur only after Artifact identity exists.
## Bounded compound-artifact inspection

`artifacts.inspect` is a governed read over the concrete enrolled representation. Its byte probe uses the same retained-root containment as local source reads, is bounded to 2 MiB, and raw bytes never enter the capability result. The persisted intake records the inspection occurrence that justified the model context.

The first inspection adapters use no new dependencies. UTF-8/Markdown/HTML expose bounded textual and structural observations; DOCX/XLSX/PPTX packages are inspected through their ZIP/XML structure; image formats expose deterministic metadata only; PDF inspection deliberately reports page/image resource evidence plus unresolved text/layout/visual semantics rather than pretending PDF is text.

Inspection policy is independent from semantic classification. If the concrete Source policy blocks `inspect`, classification does not call the model and no Work is created.
