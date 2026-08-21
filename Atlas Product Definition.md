# Atlas Product Definition

**Status:** Current product definition  
**Scope:** Atlas 2.0

Atlas is a local-first persistent operational agent whose job is to reduce recurring administrative, informational and decision-making friction by owning useful responsibilities over time.

Its value comes from durable outcomes rather than merely answering prompts.

## Product promise

Atlas should increasingly remove the need to reconstruct context, manually bridge routine work, repeatedly explain the same operational picture, or supervise every small step of a task.

The defining experience is:

> Give Atlas a responsibility, and Atlas owns the durable work required to reach a verified outcome within the authority it has been granted.

## Responsibility 1 — Maintain useful operational state

Atlas should maintain an up-to-date structured representation of the operational facts that matter to the responsibilities it owns.

For the current engineering use case this can include, as capabilities are added:

- current and last-reported machine state;
- active breakdowns and unresolved work;
- incidents and safety items;
- work completed during recent shifts;
- important changes since the previous reporting cycle;
- information still requiring confirmation.

Current implementation provides the durable runtime and the Morning Workflow capability; richer cross-day operational state remains product work rather than a claim of present completion.

### Definition of done

Atlas can answer operational questions from durable state and evidence without requiring the user to reconstruct the same picture from source material every time.

## Responsibility 2 — Own durable tasks and follow-up

Substantive work must survive the conversation in which it was requested.

Atlas represents meaningful work as durable tasks containing objectives, success criteria, constraints, authority, steps, executions, artifacts, evidence, approvals, checkpoints and event history.

Examples of responsibilities that fit this model include:

- maintenance actions;
- safety actions;
- inspection findings;
- training requirements;
- commitments;
- outstanding information;
- reminders and follow-up dates;
- decisions waiting on another person or system;
- long-running research, indexing or analysis.

### Definition of done

Atlas can resume, inspect and complete work from durable task truth rather than relying on a chat transcript or model memory.

## Responsibility 3 — Build durable knowledge and operational memory

Atlas should become more useful because it has been operating over time.

The current knowledge plane provides SQLite-first text ingestion, chunk provenance and FTS retrieval. Future operational memory may connect facts such as:

```text
machine
  → fault
  → symptoms
  → repair
  → parts
  → reported state
  → previous occurrence
  → relevant technical source
```

Document indexing by itself is not the end goal. The goal is evidence-backed retrieval that can be connected to real operational work.

### Definition of done

Atlas can retrieve relevant history and source material while distinguishing recorded fact from inference.

## Responsibility 4 — Complete bounded work

Atlas should not stop at analysis when the authorised outcome is something it can reliably produce or execute.

Depending on available capabilities and granted authority, this can include:

- generating reports;
- indexing or retrieving knowledge;
- creating artifacts;
- updating internal state;
- preparing meeting briefs;
- drafting or sending communications;
- invoking tools or APIs;
- running deterministic workflows;
- producing action lists, forms or logs.

The current runtime enforces this through:

```text
Task → Capability → Artifact → Verification
```

### Definition of done

An authorised bounded task can move from request or trigger to durable task formation, execution, verification and presentation without unnecessary prompting between each step.

## Responsibility 5 — Use intelligence as a replaceable capability

No model is Atlas.

Atlas may route planning, reasoning, coding, document interpretation or other intelligence capabilities to local or cloud providers. Provider choice is governed by capability contracts, privacy rules, context capacity, routing policy and progressively Atlas-specific evaluation scores.

A different provider may satisfy the same capability without changing task semantics.

## Responsibility 6 — Remain useful across interfaces

Conversation is one interface, not the product boundary.

Current and intended interfaces include:

- CLI for engineering and recovery;
- offline-first supervisor Mobile Capture;
- LAN-local Atlas Companion PWA (Ask / Work / Knowledge / Models / Settings);
- future authenticated remote Companion access, APIs, schedules, events and integrations.

All interfaces must enter the same durable Atlas runtime rather than create separate agent identities or competing stores of truth.

## Current product state

Implemented on `main`:

- durable WorkRuntime / WorkEngine;
- SQLite task / step / execution / artifact / claim / approval / checkpoint / event state;
- CapabilityDefinition catalog, versioned execution profiles, and Work registrations;
- bounded ContextBuilder and immutable ContextManifest;
- verification and completion gates;
- explicit authority and approvals;
- tool gateway and MCP adapter boundary;
- local/cloud provider routing and eval score persistence;
- SQLite/FTS knowledge plane;
- Morning Workflow integration;
- offline-first Mobile Capture surface with phone-offline acceptance;
- LAN-local Companion PWA over the same TaskRuntime;
- inferred Ask criteria/authority, persisted Ask conversations, and exclusive cloud/local model activation;
- intent-based presentation profiles separate from capability defaults.

Not yet implemented as canonical production surfaces:

- authenticated remote Companion access;
- authenticated Mobile Capture server synchronization;
- production always-on server packaging;
- resource-management capabilities for shared host workloads;
- semantic/vector retrieval unless future evals justify it.

## Product boundary

Atlas is not a collection of autonomous named agents, a giant system prompt, a chat history, a fixed reasoning mesh, or a wrapper around one model.

**Atlas is the persistent operational system that owns objectives, state, evidence, authority, execution and completion while composing the smallest set of capabilities needed to remove real recurring friction.**
