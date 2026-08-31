# P5 — Multi-Representation Knowledge Ingest

## Purpose

P5 removes the one-representation bottleneck in `knowledge.ingest` while preserving Atlas's topology:

- the model states the semantic representation needs;
- the runtime owns execution, authority, provenance, persistence, and verification;
- all semantic model work goes through the configured AI provider boundary;
- deterministic extraction remains deterministic infrastructure.

The model-facing semantic needs are `text`, `layout`, `tables`, and `visual`. OCR is not a semantic need. It is a runtime mechanism that may be used when searchable text cannot be obtained natively.

## Runtime plan

For one Artifact responsibility, `knowledge.ingest` builds one Work item and one building Knowledge generation.

`text` is satisfied through deterministic text extraction. `layout`, `tables`, and `visual` are combined into one governed `representations.interpret` model call so the same document is not resent once per semantic need.

Every resulting representation is indexed into the same building generation. Verification requires every representation produced by that Work to have current passages before the generation can become a candidate.

## Model boundary

`ModelRequest` is provider-neutral and can carry text, image, and document content parts. Anthropic serializes those parts into native Messages API content blocks. Other adapters currently reject unsupported multimodal requests explicitly rather than discarding media.

`representations.interpret` accepts an Artifact plus one or more of `layout`, `tables`, and `visual`. For PDFs it supplies the governed PDF bytes to the configured AI model; for supported images it supplies the image bytes. The resulting semantic representation is persisted as a derived Artifact.

Model-derived provenance records the source byte hash, provider, model, requested semantic needs, prompt version, and `reproducible: false`. Stored output is reused as evidence rather than being treated as deterministic source truth.

## Verification and activation

The workflow is approximately:

1. derive searchable text when requested;
2. index it into the building generation;
3. derive one combined semantic model representation when layout/table/visual understanding is requested;
4. index that representation into the same generation;
5. verify passage integrity and required representation coverage;
6. require the existing confirmation gate before activation.

A missing representation cannot silently disappear while the generation still activates.

## Current bounds

The first implementation uses direct governed PDF/image input for semantic interpretation and caps raw direct document bytes at 20 MiB so base64 transport stays below common provider request limits. Large-document splitting/page selection remains a runtime concern and must not be delegated to the model as an authority decision.

The current Raptor maintenance manual is about 8.5 MiB and 139 pages, so it falls within the direct-PDF path supported by the configured Claude model. Larger manuals that exceed the direct request bound will require page-wise or section-wise interpretation before they can use the same P5 workflow.

## Invariants

- one Artifact responsibility creates one Work item;
- multiple representations contribute to one Knowledge generation;
- the model never emits workflow steps or selects providers;
- OCR remains an extraction fallback, not a semantic request;
- model perception does not bypass Atlas policy;
- activation remains deterministic and confirmation-gated;
- provider-specific multimodal syntax stays inside provider adapters.
