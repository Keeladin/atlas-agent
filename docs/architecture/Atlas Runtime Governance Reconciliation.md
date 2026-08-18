# Atlas 2.0 — Runtime Governance Reconciliation

**Status:** Approved architecture reconciliation  
**Date:** 18 August 2026  
**Authority order:** Atlas Constitution → `Atlas Architecture — Runtime and Topology.md` → this reconciliation → advisory source documents.

## Purpose

This record reconciles the two August 18 implementation advisory documents with the canonical
Atlas 2.0 runtime topology. The advisory source documents remain design inputs; where a source is
mirrored under `docs/architecture/advisory/`, it is retained as advisory rather than promoted to
canonical architecture. They do not create a competing architecture.

## Accepted mappings

### Specialist Contract → Capability Contract

Atlas remains one persistent agent. The advisory's bounded specialist contract is implemented
as the versioned `CapabilitySpec` contract. Planning, research, coding, analysis, data work and
action execution are execution profiles/capabilities, not autonomous agent identities.

A capability contract may declare:

- stable id + SemVer version;
- name, description and bounded objective;
- input/output schemas;
- allowed ToolDescriptor references;
- authority requirement;
- side effects and idempotency;
- context profile + ContextPolicy;
- eligible providers and privacy routing;
- data classification;
- verifier/retry policy;
- execution/tool/cost budgets;
- tags, deprecation and replacement metadata.

Task steps may pin an exact capability version. Every execution persists the exact version used.

### ToolDescriptor → accepted substantially

`ToolDescriptor` is the declarative, versioned description of a native/API/CLI/MCP tool surface.
Handlers remain injected implementation details.

Descriptors carry the contract-level information needed for least privilege and auditability:
origin, input/output schemas, permissions, constraints, auth reference metadata, side-effect
classification, privacy classification, deprecation and version.

Credentials are never stored in descriptors.

### ContextManifest → accepted as a runtime invariant

`ContextBuilder` is the sole component authorised to construct model/capability context.
Every normal capability/model invocation gets a ContextManifest that is:

- produced from durable task state;
- written before the handler/provider call;
- immutable for that execution;
- SHA-256 hashed;
- linked to task, step, execution and exact capability version;
- bounded by the capability ContextPolicy;
- explicit about included and dropped artifacts/tool descriptors;
- explicit about token estimates and bucket accounting;
- linked to the previous manifest on bounded rework where applicable.

Planning is not exempt: planning uses the same ContextBuilder and persists its manifest before
the planning provider is invoked.

Human approval gates are durable authority decisions rather than model context invocations and
do not require a model ContextManifest.

## MCP reconciliation

MCP is a preferred external integration protocol **when a suitable MCP implementation exists**.
It is not Atlas's internal ontology and not mandatory for a capability.

```text
Capability
   ↓
Execution adapter
   ├─ native Python
   ├─ REST/API
   ├─ CLI
   └─ MCP
```

Atlas Platform Services such as Task Store, evidence state and runtime events remain internal.

## Retrieval reconciliation

SQLite/FTS remains the implemented first retrieval backend. The advisory hybrid formula

`semantic × 0.7 + recency × 0.2 + importance × 0.1`

is retained as a configurable ContextPolicy starting point, not a hard-coded ranking truth.
Semantic/vector retrieval must earn deployment through retrieval evals. When a vector backend is
introduced, normalization on write/query and embedding-model identity become backend invariants.

## Evidence reconciliation

The advisory requires evidence-bearing specialist output. Atlas enforces the stronger runtime
boundary: accepted task criteria require durable evidence artifacts, observed/retrieved/
calculated/executed claims require evidence references, and successful external side effects
require receipts. A model output does not need to invent its own `evidence[]` array when the
runtime itself has authoritative artifact/receipt provenance.

## Versioning and pinning

- Capability Registry is version-aware.
- Tool Gateway is version-aware.
- Unpinned lookup resolves the newest active version.
- Durable planned steps pin capability versions.
- Executions always record the exact capability version.
- Deprecated versions remain addressable only when explicitly pinned; they are never selected
  by unpinned lookup.

## Context ownership invariant

No Planner, provider adapter, verifier, Tool Gateway, prompt helper or domain workflow may build
an independent model context outside `ContextBuilder`.

Providers receive projections created by the ContextBuilder. Provider adapters may translate
that projection into provider wire format, but may not add task facts or hidden domain context.

## Deliberately deferred items

The following advisory ideas remain valid future options but are not implementation requirements
until evals/workloads justify them:

- vector database;
- semantic/hybrid retrieval backend;
- fixed 0.7/0.2/0.1 production weights;
- external MCP memory server;
- broad always-on Tier-0 MCP fleet;
- independent named specialist agents.

## Implementation proof required

Regression coverage must prove at minimum:

1. capability version pinning survives durable execution;
2. ContextManifest exists before a provider/handler invocation;
3. a ContextManifest cannot be overwritten for an execution;
4. bounded context records dropped candidates and reasons;
5. tool/capability input and output schemas are enforced;
6. tool constraints fail closed;
7. planning uses ContextBuilder and pins planned versions;
8. existing Morning/Mobile behavioural suites remain green.
