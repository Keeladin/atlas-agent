# Atlas 2.0 — Runtime Governance

**Status:** Current canonical runtime governance  
**Date reconciled:** 18 August 2026  
**Authority order:** Atlas Constitution → Runtime and Topology → this document → domain behavioural contracts

This document defines the implementation-level governance rules that keep Atlas 2.0 consistent with the Constitution and current runtime topology.

It is not an advisory or historical reconciliation record. These are the current rules.

---

## 1. Capability contract is the execution contract

Atlas remains one persistent agent.

Planning, research, coding, analysis, data work, tool use and action execution are represented as bounded capabilities or execution contexts, not autonomous named agents.

`CapabilitySpec` is the canonical versioned capability contract.

A capability may define:

- stable ID;
- SemVer version;
- human description and bounded responsibility;
- executor kind;
- input/output schemas;
- allowed ToolDescriptor references (runtime execution-frame allow-list, not vendor identity);
- required authority;
- confirmation requirement (`none` or `required`; action property, not authority approval; not enforced yet);
- side-effect classification;
- idempotency;
- context policy;
- eligible providers;
- privacy/data classification;
- verifier;
- execution/tool/cost budgets;
- retry behaviour;
- parallel safety;
- deprecation/replacement metadata.

Durable planned steps may pin an exact capability version. Every execution records the exact version used.

Discovery never equals exposure. Deployment bindings (`CapabilityBinding`) are not part of capability identity and do not grant authority. Unmapped provider tools are not capability consumers' tools. See [Capability Awareness](./Capability%20Awareness.md) and [Security and Intent Model](./Atlas%20Security%20and%20Intent%20Model.md).

---

## 2. ToolDescriptor is declarative, not executable authority

`ToolDescriptor` is the versioned description of a native, API, CLI or MCP tool surface.

Descriptors can carry:

- ID and version;
- origin;
- input/output schema;
- permissions;
- allowed roots/commands/operations;
- timeout/result limits;
- auth-reference metadata;
- side-effect class;
- privacy classification;
- deprecation/replacement metadata.

Credentials are never stored in descriptors.

A tool being registered does not automatically grant a task authority to use it.

---

## 3. ContextBuilder owns model/capability context assembly

No planner, provider adapter, verifier, Tool Gateway, prompt helper or domain workflow may invent a parallel task-context assembly path.

`ContextBuilder` is the sole runtime component authorised to construct model/capability context projections from durable task state.

Every normal capability/model invocation receives a `ContextManifest` that is:

- linked to task, step and execution;
- linked to the exact capability version;
- produced from durable state;
- persisted before the handler/provider invocation;
- immutable for that execution;
- SHA-256 identified;
- bounded by the capability ContextPolicy;
- explicit about included and dropped material;
- explicit about reasons, estimates and bucket accounting;
- linked to prior rework context where applicable.

Planning is not exempt. Planning uses the same ContextBuilder boundary.

Human approval gates are durable authority decisions rather than model-context invocations and do not require a model ContextManifest.

---

## 4. Context is a projection, not memory

Atlas state lives outside the model context.

A context window is temporary workspace assembled for one bounded execution frame. It must not become the durable source of task truth.

The runtime may preserve large artifacts by identity/hash/metadata while retrieving only the content required for the current execution.

Capability and presentation remain separate. ContextBuilder may still invoke `reasoning.general` while assembling a concise-answer, conversational, compose, evidence, or research profile from the user objective. A capability default of `research` is not a presentation contract.

Long task depth is achieved through many bounded frames, not by growing context indefinitely.

---

## 5. TaskRuntime owns execution depth

Execution depth is a runtime governance concern, not a conversational Director/tool-round concern.

The current runtime budget type supports ceilings including:

- maximum executions;
- maximum cycles;
- maximum model calls;
- maximum parallel workers;
- optional cost budget.

Budgets are safety/resource ceilings. Atlas should complete early when success criteria are satisfied.

Retries and rework create new execution records rather than resetting history or rewriting failed attempts.

Provider escalation does not erase prior execution depth.

---

## 6. Evidence boundary

Artifacts, claims and receipts are the durable provenance layer.

Atlas distinguishes claim classes such as:

```text
observed
retrieved
calculated
inferred
suggested
executed
```

Observed, retrieved, calculated and executed claims require appropriate evidence references.

Successful external side effects require receipts. A model-generated sentence claiming success is not an execution receipt.

Task success criteria are accepted only through evidence-backed verification.

---

## 7. Verification and completion are separate from generation

A capability output may end in:

```text
pass | rework | abstain | fail | blocked
```

A successful-looking model response is not automatically a `pass`.

Capability verification evaluates the bounded execution output. Task completion is a separate gate over task criteria, accepted evidence, blocked work and pending authority decisions.

Rework creates another execution attempt; the previous attempt remains immutable durable truth.

---

## 8. Authority is explicit

Authority levels are monotonic:

```text
read
  → interpret
  → recommend
  → modify_internal
  → communicate
  → execute_external
```

Capabilities declare minimum required authority.

When task authority is insufficient, Atlas creates a durable approval request and blocks the bounded action.

Approval applies to that action. It must not silently elevate the entire task or user session.

Non-idempotent side effects are never blindly retried because a model or verifier asks for rework.

---

## 9. Provider governance

Models are providers beneath capabilities.

Provider adapters may translate a ContextBuilder projection into provider wire format, but they may not add hidden task facts or bypass Atlas context governance.

Routing can consider:

- capability eligibility;
- Atlas-specific eval score;
- provider allowlists;
- privacy/local-only constraints;
- context capacity;
- priority;
- latency rank;
- configured cost information;
- current availability.

Neutral seed scores are placeholders until Atlas-specific evals provide measured competence.

A failed or abstaining provider attempt remains part of durable execution truth even when a later retry uses another provider.

---

## 10. Evaluation governance

Atlas includes a capability evaluation harness.

Provider roles are earned through measured capability performance rather than hard-coded prestige or model size.

Useful reliability measures include:

- repeated attempts;
- `pass@1`;
- `pass@k`;
- `pass^k`.

Measured scores can persist by provider + capability so routing competence survives restarts.

Evals should measure Atlas responsibilities, not only generic model benchmarks.

---

## 11. Retrieval governance

SQLite/FTS is the current implemented retrieval backend.

Semantic/vector retrieval is deliberately deferred until retrieval evaluations demonstrate a real need.

If a semantic backend is later introduced:

- it implements the existing retrieval capability boundary;
- embedding model identity is persisted;
- vector normalization is enforced consistently;
- ranking weights are configuration/policy rather than universal truth;
- provenance remains artifact/source based;
- the vector system does not become a competing memory ontology.

No fixed hybrid formula is a current production invariant.

---

## 12. MCP boundary

MCP is an external integration protocol, not Atlas's internal ontology.

```text
Capability
   ↓
Execution adapter
   ├── native Python
   ├── REST/API
   ├── CLI
   └── MCP
```

`MCPToolBridge` normalizes discovered MCP tools through the same Tool Gateway/capability contracts as native tools.

Transport choice belongs at the edge and may be stdio, HTTP, vendor-hosted or connector-provided.

Atlas does not require a permanent always-on MCP fleet.

---

## 13. Event and observability boundary

Runtime lifecycle events are durable and may also be fanned out to subscribers.

Examples include:

```text
task.started
task.paused
task.resumed
task.completed
task.failed
capability.started
capability.completed
verification.completed
approval.requested
approval.applied
retry.blocked
```

Telemetry, notifications, cost accounting and future observability integrations may consume these events without becoming orchestration logic.

---

## 14. Domain workflow governance

Existing vertical responsibilities integrate through ordinary capability boundaries.

### Morning Workflow

`atlas_morning/` remains a deterministic domain workflow and is exposed as:

```text
operations.morning_pack.generate
```

The general runtime owns task/execution/evidence state while the frozen behavioural contract owns domain meaning.

### Mobile Capture

`atlas_mobile/` is an offline-first interface/domain capture surface.

Its local IndexedDB state is necessary for disconnected operation but is not a second Atlas server runtime. Future synchronization must enter the canonical Atlas API/runtime through an authenticated idempotent ingest boundary.

---

## 15. Current implementation layout

```text
atlas_core/
├── tasks/
│   └── durable task/step/execution/artifact/claim/approval/checkpoint/event state
├── capabilities/
│   └── CapabilitySpec contracts + version-aware registry
├── providers/
│   └── provider contracts, routing, HTTP adapters and eval scores
├── knowledge/
│   └── SQLite/FTS ingestion and retrieval
├── integrations/
│   └── adapters around domain responsibilities
├── authority.py
├── context.py
├── deliverable.py
│   └── deliverable contract + presentation profile
├── runtime.py
│   └── TaskRuntime public facade
├── runtime_types.py
├── runtime_lifecycle.py
├── runtime_execution.py
├── runtime_finish.py
├── verification.py
├── planner.py
├── presentation.py
├── tools.py
├── evals.py
├── events.py
├── schema_validation.py
├── bootstrap.py
└── __main__.py

atlas_companion/ LAN-local Companion PWA adapter
atlas_morning/   frozen Morning Workflow implementation
atlas_mobile/    offline-first Mobile Capture surface
```

---

## 16. Explicitly deferred infrastructure

The following are not current runtime requirements:

- vector database;
- semantic/hybrid retrieval backend;
- independent named specialist agents;
- permanent broad MCP fleet;
- Temporal/Celery merely to claim task durability;
- Kubernetes/Kafka/microservices without workload evidence;
- general web interface as a source of runtime truth.

They may be introduced later only when a real responsibility, reliability requirement or evaluation demonstrates the need.

---

## 17. Regression proof required

Architecture claims are only meaningful when executable behaviour supports them.

Regression coverage should preserve at minimum:

1. durable state survives database reopen;
2. dependency graph controls ready work;
3. artifacts are immutable and hashed;
4. retries create new executions;
5. capability version pinning survives durable execution;
6. ContextManifest exists before provider/handler invocation;
7. ContextManifest cannot be overwritten;
8. dropped context candidates and reasons remain auditable;
9. tool/capability schemas are enforced;
10. tool constraints fail closed;
11. planning uses ContextBuilder;
12. authority can block and resume one bounded action;
13. provider routing respects privacy and eval scores;
14. side-effecting tools require receipts;
15. tasks can execute beyond shallow conversational round limits;
16. presentation profile follows intent rather than capability default;
17. Morning, Mobile, and Companion behavioural regression suites remain green.

---

## 18. Canonical runtime rule

The implementation-level shorthand remains:

```text
Task → Capability → Artifact → Verification
```

The TaskRuntime owns objectives, durable state, authority, execution history and completion. Capabilities perform bounded responsibilities. Artifacts/claims/receipts preserve evidence. Verification decides whether the work actually satisfies its contract.
