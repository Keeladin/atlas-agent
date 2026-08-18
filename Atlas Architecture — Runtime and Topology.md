# Atlas Architecture — Runtime and Topology v0.2

Status: **approved for full implementation**

This is the canonical Atlas 2.0 general-runtime architecture. It is based on the Atlas
Constitution, the working `atlas-agent` vertical slices, lessons from the original `atlas`
repository, and useful runtime patterns surveyed from Everything Claude Code.

It does **not** replace or rewrite the frozen Morning Workflow or Mobile Capture behaviour.
Those remain domain responsibilities and are integrated through capability boundaries.

## 1. Product definition

Atlas is one persistent operational agent.

Models, tools, APIs, deterministic programs, specialist prompts, parsers, retrieval systems,
MCP servers and cloud services are capabilities Atlas may invoke. They do not become
independent Atlas identities and they do not own durable operational state.

The runtime owns objectives, evidence, authority, execution state and completion. A
conversation is an interface to Atlas, not the source of truth.

## 2. Architectural invariants

1. **One Atlas.** Specialists are bounded capabilities or contexts, not persistent personas.
2. **Tasks are durable.** Substantive work survives model calls, context limits and process restarts.
3. **Task depth is not tool-round depth.** Safety budgets limit execution frames and total resource use, not arbitrary conversational rounds.
4. **Deterministic work stays deterministic.** Calculation, filtering, state transitions, schema validation and known business rules prefer ordinary code.
5. **Capabilities have contracts.** Inputs, outputs, side effects, authority, budgets, retry behaviour and verification are explicit.
6. **Models are providers, not architecture.** Provider selection may change without changing task semantics.
7. **Context is assembled, not accumulated.** Each bounded execution receives only the state and evidence required for its current responsibility.
8. **State lives outside model context.** Operational truth is persisted structurally.
9. **Evidence and derived claims remain distinguishable.** Observed, retrieved, calculated, inferred, suggested and executed facts are different epistemic classes.
10. **Verification precedes completion.** A model saying `done` is not evidence that success criteria were met.
11. **Authority is explicit.** Read, interpret, recommend, modify internal state, communicate and external execution are distinct permission levels.
12. **Checkpoints make long work resumable.**
13. **Learning is proposed, not silently installed.** One-off corrections do not become global rules automatically.
14. **Complex infrastructure must be earned by a real responsibility.**
15. **Existing domain workflows are capabilities, not the agent runtime.**

## 3. Canonical topology

```text
USER / EVENT / SCHEDULE / MOBILE / API
                  |
                  v
             INTERFACES
                  |
                  v
        +-------------------+
        |     TASK PLANE    |
        | objective         |
        | success criteria  |
        | constraints       |
        | authority         |
        | durable status    |
        +---------+---------+
                  |
                  v
        +-------------------+
        |    TASK RUNTIME   |
        | ready work        |
        | dependencies      |
        | task budgets      |
        | checkpoints       |
        | retries           |
        | parallel-safe     |
        +----+---------+----+
             |         |
             v         +------------------------------+
      CONTEXT BUILDER                                 |
             |                                        |
             v                                        |
      CAPABILITY REGISTRY                             |
             |                                        |
    +--------+-----------+-------------+              |
    |        |           |             |              |
    v        v           v             v              |
 deterministic       tool/API      model         human gate
    |        |           |             |              |
    |        |           |             v              |
    |        |           |       MODEL ROUTER         |
    |        |           |   local / cloud providers  |
    +--------+-----------+-------------+              |
             |                                        |
             v                                        |
          ARTIFACTS                                   |
             |                                        |
             v                                        |
         CLAIMS / RECEIPTS                            |
             |                                        |
             v                                        |
        VERIFICATION ---------------------------------+
             |
       pass / rework /
       abstain / fail /
          blocked
             |
             v
       TASK STATE UPDATE
             |
       more work? ---- yes ---> TASK RUNTIME
             |
             no
             v
    COMPLETION VERIFIER
             |
             v
       PRESENT / COMMIT
```

## 4. Durable object model

### Task

The durable owner of meaningful work:

- objective;
- explicit success criteria;
- constraints;
- authority scope;
- status;
- metadata;
- steps;
- executions;
- artifacts/evidence;
- claims;
- approvals;
- checkpoints;
- event history.

### Success criterion

Each criterion is independently persisted and may be:

`pending | accepted | rejected | unknown`

An accepted criterion references evidence artifacts. Completion is impossible while required
criteria remain unresolved.

### Step

A bounded unit of work:

- dependencies;
- desired capability;
- explicit input artifacts;
- status;
- metadata including which criteria it may satisfy.

Independent ready steps may run concurrently only when their capability contract declares
`parallel_safe`.

### Execution

One concrete attempt to satisfy a step:

- capability;
- selected provider/tool;
- attempt number;
- inputs;
- outputs;
- verifier artifact;
- receipt;
- metrics;
- terminal outcome.

Terminal execution truth is:

`pass | rework | abstain | fail | blocked`

A retry creates a **new execution record**; it never rewrites the previous outcome.

### Artifact

Immutable task input or output with SHA-256 identity and metadata. Large binary storage may be
moved to a content-addressed file store later without changing task semantics.

### Claim

A durable statement with one epistemic class:

`observed | retrieved | calculated | inferred | suggested | executed`

Observed/retrieved/calculated/executed claims require evidence references. This prevents model
prose from silently becoming operational truth.

### Approval

A durable, per-action authority decision. A task may be capable of an action without having
permission to perform it. Approval can unblock one bounded action without globally elevating
Atlas authority.

### Checkpoint

A durable task snapshot at a logical execution boundary. Checkpoints reference artifact hashes
and IDs rather than duplicating large payloads.

## 5. Capability contract

Every executable responsibility has one capability specification:

```text
id
human description
executor kind
input schema
output schema / artifact kind
required authority
side effects
context profile
eligible providers
privacy rule
budget
retry policy
idempotency
parallel safety
verifier
metadata / criterion mapping
```

Executor kinds:

- `deterministic`;
- `tool`;
- `model`;
- `composite`;
- `human`.

The existing Morning Workflow is exposed as a deterministic composite capability without
rewriting the parser or changing its behavioural contract.

## 6. Runtime lifecycle

```text
INGEST REQUEST/EVENT
        |
        v
FORM DURABLE TASK
        |
        v
PLAN IF NECESSARY
        |
        v
DEPENDENCY GRAPH
        |
        v
SELECT READY STEP(S)
        |
        v
ASSEMBLE BOUNDED CONTEXT
        |
        v
CHECK AUTHORITY
        |
        +--> insufficient --> durable approval / waiting
        |
        v
SELECT CAPABILITY / PROVIDER
        |
        v
EXECUTE BOUNDED FRAME
        |
        v
STORE OUTPUT + RECEIPT + CLAIMS
        |
        v
VERIFY
  |       |       |       |
 pass   rework  abstain  fail/blocked
  |       |       |       |
  +-------+-------+-------+
          |
      checkpoint
          |
          v
   more ready work?
     |          |
    yes         no
     |          |
 runtime     completion gate
                |
           criteria/evidence
                |
          complete / wait / fail
```

Task depth can be thousands of frames. Each frame remains bounded and independently auditable.
The execution row is created atomically before any handler/model/tool runs. Explicit recovery can
convert interrupted idempotent work into a retryable new attempt, while interrupted non-idempotent
side effects fail closed because external state may be unknown. Optional task/capability cost
budgets preflight priced provider calls and normalized provider usage is retained on executions.

## 7. Context topology

Contexts such as `research`, `plan`, `execute`, `review`, `verify` and `present` are operating
profiles, not agents.

For each execution frame Atlas builds a fresh context projection from durable state. Direct step
inputs and dependency outputs remain separately identified inside the capability request. Oversized
artifacts are represented by identity/hash/metadata rather than silently truncating the task's
objective, criteria or constraints. A deterministic capability may explicitly retrieve its direct
artifact by ID; a later reasoning step may request omitted content through retrieval.

The context window is a workspace, not a database.

## 8. Verification topology

Verification is capability-specific and deterministic where possible.

Examples:

- Morning pack: structural output contract and existing acceptance suite;
- indexing: chunk/page coverage and source reconstruction;
- coding: tests/static checks/diff review;
- external communication: successful provider receipt/message identity;
- analytical recommendation: evidence-grounding checks.

Task completion is an independent gate over accepted step outputs, success criteria and pending
approvals.

## 9. Model/provider topology

Model providers live under capabilities.

The runtime includes provider adapters for:

- local/OpenAI-compatible chat endpoints (including LM Studio-style endpoints);
- OpenAI Responses API;
- Anthropic Messages API;
- Gemini Generate Content API;
- other OpenAI-compatible gateways such as xAI through configuration.

Cloud providers are disabled by default in example configuration and credentials are read from
environment variables. The example includes current OpenAI (`gpt-5.6` alias), Gemini
(`gemini-3.6-flash`) and xAI (`grok-4.5`) identifiers plus an explicit Anthropic model placeholder
that must be set from the account's current model catalogue. Provider identities and model IDs are
configuration, not business logic.

Routing considers:

- capability competence score;
- eval-score overrides;
- local/cloud privacy constraint;
- provider allowlists;
- context capacity;
- priority and latency rank.

A failed/abstaining model attempt remains durable execution truth. A subsequent retry may route to
another provider without hiding the failed attempt.

## 10. Evals

Atlas includes a capability eval harness with:

- deterministic or model/human graders supplied by the caller;
- repeated attempts;
- `pass@1`;
- `pass@k` (at least one success in k);
- `pass^k` (all k attempts succeed).

Measured scores override neutral provider eligibility seeds in routing and are persisted in SQLite
by provider + capability, so earned competence survives restarts. Model roles are therefore earned
through Atlas-specific evals rather than prestige, size, or a hard-coded static hierarchy.

## 11. Local knowledge and retrieval

Atlas includes a SQLite-first knowledge plane for extracted text. `knowledge.ingest_text` chunks and
persists source material with document/chunk hashes and provenance; `knowledge.search` returns
source-grounded chunks through the same capability/artifact/claim runtime. FTS5 is used when
available, with a deterministic SQLite fallback. This is sufficient to exercise large-manual
ingestion and bounded iterative retrieval without introducing a vector service before measured
semantic-retrieval need exists. Embedding/vector retrieval can later implement the same retrieval
contract without changing task ownership.

## 12. Tool and MCP boundary

Native tools and APIs pass through a normalized Tool Gateway. Side-effecting tools must return a
receipt; success without a receipt is rejected at the boundary.

MCP is an **adapter**, not Atlas's internal ontology. `MCPToolBridge` accepts a transport client,
discovers tools, and exposes them through the same Tool Gateway/capability contracts used by native
tools.

The wire transport itself is intentionally not embedded in core runtime because Atlas may use
stdio, HTTP, vendor-hosted or connector-provided MCP clients. Transport choice belongs at the edge.

## 13. Authority and safety

Authority levels are monotonic:

```text
read
  -> interpret
  -> recommend
  -> modify_internal
  -> communicate
  -> execute_external
```

A capability declares its minimum required level. Insufficient authority creates a durable approval
request and pauses the step. Approval applies to that bounded action rather than silently raising
the whole task's authority.

Non-idempotent side effects are not automatically retried merely because a model or verifier asks
for rework.

## 14. Events and observability

Runtime lifecycle events are persisted in SQLite and may also be published to an in-process event
bus:

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

Audit, telemetry, cost accounting, notifications and future learning proposals may consume events
without becoming orchestration logic.

OpenTelemetry can later subscribe at this boundary without changing execution semantics.

## 15. Current implementation layout

```text
atlas_core/
  tasks/          durable SQLite task/step/execution/artifact/claim/approval/event state
  capabilities/   contracts, registry, canonical intelligence capabilities
  providers/      provider contracts, routing, durable eval scores, configuration and HTTP adapters
  knowledge/      SQLite FTS ingestion/search with chunk provenance
  integrations/   adapters around existing vertical responsibilities
  authority.py    authority ladder and decisions
  context.py      bounded frame context assembly
  runtime.py      task execution engine and interrupted-frame recovery
  verification.py capability + completion verification
  planner.py      strict durable planning execution -> task graph
  presentation.py deterministic evidence-backed task presentation
  tools.py        normalized tools + MCP bridge
  evals.py        capability reliability harness
  events.py       isolated runtime event fan-out
  bootstrap.py    runtime assembly
  __main__.py     CLI for plan/run/recover/approve/result/knowledge/Morning
```

Existing:

```text
atlas_morning/    frozen legacy/import morning workflow (kept intact)
atlas_mobile/     offline-first mobile capture surface (kept intact)
```

## 16. Deliberate external boundaries

The full runtime topology is implemented without pretending that every possible external system is
already configured.

These remain edge integrations for valid reasons:

- **Live cloud calls require user credentials and current model IDs.** Adapters and configuration
  exist; providers stay disabled until configured.
- **MCP wire transport depends on the actual MCP server/transport selected.** The bridge contract is
  implemented; transport is injected rather than hardcoded.
- **Semantic/vector retrieval is not required by the current Morning/Mobile responsibilities.** The
  artifact/claim model is ready for it without forcing a vector database into the runtime.
- **Temporal/Celery are not required to achieve durable task depth locally.** SQLite task state and
  checkpoints already survive process restarts. A workflow backend can replace the execution
  scheduler later if a real workload proves local scheduling insufficient.
- **A new web/chat UI is not required to establish runtime truth.** The runtime exposes Python and
  CLI entry points; UI should be built against the stable task contract rather than define it.

These are not incomplete hidden subsystems; they are explicit adapters around the canonical core.

## 17. Validation requirements

The architecture is not considered sound merely because modules import.

Regression requirements include:

- current Morning/Mobile tests remain unchanged and passing;
- durable state survives database reopen;
- dependency graph controls readiness;
- artifacts are immutable and hashed;
- execution records are terminal once written;
- rework creates new attempts rather than rewriting history;
- authority can pause and resume one bounded action;
- context overflow preserves artifact references instead of inventing/truncating truth;
- model routing respects privacy and eval score;
- provider abstention can fail over without hiding the failed attempt;
- tasks can execute well beyond the old five-round ceiling;
- side-effecting tools require execution receipts;
- checkpoints never duplicate large artifact payloads.

## 18. What was reused and what was rejected

### From `atlas-agent`

Keep: outcome-first specifications, deterministic processing, preserved raw evidence, acceptance
tests, offline-first discipline and refusal to infer missing operational facts.

### From original `atlas`

Keep conceptually: capability identity, provider separation, execution artifacts, verification,
runtime truth, authority and normalized outcomes.

Reject as topology: conversational Director owning execution depth, fixed Reasoning Mesh station
sequence, arbitrary tool-round completion and accumulated migration scaffolding.

### From Everything Claude Code

Adapt: bounded specialist contexts, verification loops, eval-driven development, context management,
lifecycle hooks/events and safe parallelism.

Reject: multiple persistent named-agent identities as Atlas's product ontology.

## 19. North-star definition

> Atlas is a local-first persistent operational agent whose durable runtime owns objectives, state,
> evidence, authority and completion, and dynamically assembles deterministic capabilities, tools
> and benchmarked local or cloud intelligence into bounded verified execution frames.

The resident model is not Atlas. The cloud expert is not Atlas. The Morning Workflow is not Atlas.
The runtime that owns the responsibility and its durable truth is Atlas.
