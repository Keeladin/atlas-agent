# ATLAS 2.0 (atlas-agent)
## Architecture Survey & Target Topology — Implementation Advisory Document

**Document status:** Advisory / Implementation Consideration  
**Source:** Consolidated analysis of the proposed ATLAS 2.0 topology diagram and subsequent design discussions  
**Date:** 18 August 2026  

---

## 1. Executive Summary

ATLAS 2.0 is proposed as a **single persistent agent** that orchestrates bounded specialists to deliver durable, evidence-based outcomes. It synthesizes lessons from:

- Keeladin/atlas-agent (new) and Keeladin/atlas (v1)
- Everything Claude Code (topology, verification loops, context management, agent roles)
- Modern agentic patterns (MCP, durable task state, verification-before-completion)

The design deliberately rejects unconstrained multi-agent swarms and domain-first architectures that become the core. Instead it centres on:

- One persistent agent (Atlas)
- Goal-driven tasks (not workflow-driven)
- Observable and verifiable execution
- State that lives in the system, not in model context
- Bounded specialists invoked under clear contracts
- Verification before completion
- Assembled (not accumulated) context
- Tool budgets that limit cycles
- Evidence over assertions

This document consolidates the topology review, MCP prioritization, capability contracts, context-assembly logic, runtime interactions, hybrid retrieval scoring, and vector-store normalization guidance into a single advisory for implementation.

---

## 2. High-Level Target Topology (Recap)

### Core Layers

1. **User & Interfaces** — Web App, Mobile, API/REST, CLI, Integrations
2. **ATLAS CORE (One Persistent Agent)**
   - Intake & Understanding
   - Planner & Decomposer
   - Orchestrator
   - Verifier
   - Synthesizer
   - Memory Manager
3. **EXECUTION LAYER (Bounded Specialists)**
   - Research Context
   - Analysis Expert
   - Coding Specialist
   - Data Specialist
   - Action Executor
4. **CAPABILITIES & TOOLS** — via MCP / Internal Tools
5. **PLATFORM SERVICES** — Task Store, Vector Store, Event Log, Cache, Config & Secrets, Auth
6. **MODEL & PROVIDER LAYER** — Local + Cloud (tiered recommendations)

### Key Architectural Invariants

1. Atlas is one persistent agent.
2. Tasks are goal-driven, not workflow-driven.
3. All execution is observable and verifiable.
4. State lives in the system, not in model context.
5. Specialists are bounded with clear contracts.
6. Verification before completion.
7. Context is assembled, not accumulated.
8. Tool budgets limit cycles, not task depth.
9. Checkpoints make long jobs durable.
10. Evidence over assertions.

### Vision & Philosophy

- **Vision:** Atlas is the operational partner that removes friction, gets real work done, and continuously gets better.
- **Philosophy:** Not about coercing a model into submission, but directing a choir of models into a symphony.
- **Outcome:** Atlas completes complex real-world tasks reliably, with evidence, efficiency and trust.

---

## 3. MCP Server Prioritization

MCP (Model Context Protocol) is the preferred integration surface for external capabilities. Prioritization follows Atlas invariants: bounded specialists, tool budgets, verification, evidence, and local-first preference where practical.

### Prioritization Criteria

1. Foundational coverage of the five specialists.
2. Official / reference implementations first (security, maintenance, schema stability).
3. Scoped & auditable access.
4. Local-first preference.
5. Evidence & artifact generation.
6. Low cognitive load on the Orchestrator (clear tool descriptions).

### Recommended Tiers

**Tier 0 – Core (MVP / nearly always-on)**

| Server | Purpose | Primary Specialists |
|--------|---------|---------------------|
| Filesystem (official) | Secure read/write/search with configurable roots | All |
| Git + GitHub (official) | Repo inspection, diffs, commits, issues, PRs | Coding, Action |
| Fetch / Web Search (Brave, Tavily, etc.) | Controlled retrieval + discovery | Research |
| Memory (official knowledge-graph) | Complements Atlas Memory Manager | Core, Analysis |
| Terminal / Shell (sandboxed) | Controlled execution | Action, Coding |

**Tier 1 – High-value (enable by task type)**

- Postgres / SQLite (Data Specialist)
- Playwright / Browser Use / Puppeteer (Research + Action)
- Slack / Teams / Discord or Gmail / Google Workspace / Microsoft 365 (Action)
- Notion / Confluence (Research / Analysis)

**Tier 2 – Domain / Automation (load on demand)**

- n8n or equivalent workflow engines
- Jira / Linear / Trello
- Calendar / Scheduling
- Monitoring / System Info
- Custom engineering systems

### Implementation Guidance

- Maintain a **Capability Registry** that maps specialists → allowed MCP tool sets.
- Enable servers dynamically according to the plan; do not expose every server on every turn.
- Enforce tool budgets per specialist invocation.
- Prefer stdio local servers for sensitive data; remote only with strong auth and audit logging into the Event Log.
- Keep Task Store, Vector Store, Event Log, Cache, Auth as Platform Services (internal), not external MCP, for control and durability.

**Suggested starting set:** Filesystem + Git/GitHub + Fetch/Search + Memory. Add Terminal, Database, Browser, and one communication surface according to workload.

---

## 4. Capability Registry & Specialist Contracts

The registry is the single source of truth for what specialists exist, what they may do, and how the Orchestrator must invoke them.

### Registry Structure

```
CapabilityRegistry
├── specialists: Map<SpecialistID, SpecialistContract>
├── tools: Map<ToolID, ToolDescriptor>          # MCP + internal
├── bindings: Map<SpecialistID, AllowedToolSet>
├── versions: semantic versioning + deprecation
└── discovery: list / filter / resolve by tags
```

### Shared Contract Schema (Base)

Every specialist implements:

- `id`, `name`, `version`, `description`, `objective`
- `input_schema` (strict JSON Schema)
- `output_schema` (strict JSON Schema + mandatory evidence refs)
- `allowed_tools` (from registry bindings only)
- `tool_budget` — `max_calls`, `max_tokens`, `max_wall_time_sec`, `max_cost_usd`
- `context_policy` — `max_tokens`, `must_include`, `must_exclude`, retrieval limits
- `success_criteria` (machine-checkable where possible)
- `verification` — required, verifier identity, rework limits
- `side_effects` — none | reversible | irreversible
- `privacy_level` — public | internal | sensitive

**Key design rules**

- Input and output are strictly typed.
- Every output **must** contain evidence (Artifact IDs or direct references).
- Tool budget is per-invocation.
- Context policy forces minimal, relevant assembly.
- Breaking changes require a new specialist version; Orchestrator can pin versions per task.

### Specialist Contracts (Summary)

**Research Context**  
Objective: Gather accurate, citable information and produce a structured research pack.  
Side effects: none.  
Typical tools: filesystem, fetch, web-search, memory, notion, github.

**Analysis Expert**  
Objective: Deep reasoning, pattern extraction, comparison, insight generation.  
Side effects: none.  
Typical tools: filesystem, memory, database-readonly, code-search.

**Coding Specialist**  
Objective: Generate, modify, test or review code; produce runnable artifacts.  
Side effects: reversible (prefer git).  
Typical tools: filesystem, git, github, terminal, database-readonly.  
Verification: required, higher rework allowance.

**Data Specialist**  
Objective: Query, transform, validate or analyse structured data.  
Side effects: reversible (writes only when explicitly allowed).  
Typical tools: database-readonly / write, filesystem, memory.

**Action Executor**  
Objective: Perform real-world side-effecting actions with full audit trail.  
Side effects: irreversible.  
Privacy: sensitive.  
Typical tools: gmail, slack, github, jira, calendar, n8n, terminal.  
Strong confirmation / dry-run gates recommended.

### Tool Binding Example Pattern

Bindings declare exact permissions, roots (for filesystem), allowed commands (for terminal), and auth references (via Platform Services secrets). The Orchestrator never invents tools at runtime.

---

## 5. Context Assembly Algorithm & Detailed Logic

Context is **assembled**, never accumulated. The Context Assembler runs immediately before every specialist invocation and produces both model-ready messages and an auditable `ContextManifest`.

### Goals

- Never dump conversation history or the entire task graph.
- Always include mandatory anchors (objective + success criteria).
- Retrieve only demonstrably relevant Memory and Artifacts.
- Respect per-specialist `max_tokens` and privacy rules.
- Produce an auditable manifest stored on the TaskStep.

### Data Structures

```
ContextBuckets {
  system, anchors, step_context, memory, artifacts, tools
}

ContextManifest {
  included: [{id, type, reason, tokens, score?}]
  dropped:  [{id, type, reason, tokens, score?}]
  total_tokens, budget, specialist_id, assembled_at
}

AssemblyConfig (from contract.context_policy) {
  max_tokens
  max_memory_items = 8
  max_artifact_items = 6
  max_recent_steps = 5
  min_relevance_score = 0.65
  per_item_token_cap = 1200
  allow_full_artifact = false
}
```

### Ordered Assembly Steps

1. **Initialize** empty buckets and manifest; load config.
2. **System layer** (fixed cost) — specialist system prompt, Constitution rules, output-schema reminder, evidence instruction.
3. **Mandatory anchors** (never dropped) — task.objective, task.success_criteria, step.description, optional input_summary. On rework also inject previous output summary + verification failure reason.
4. **Recent verified steps** (short horizon, default last 3–5) — output summaries + evidence refs only.
5. **Memory retrieval** (bounded hybrid RAG) — build query from objective + step; retrieve with privacy filters and min score; rank by hybrid score; add until item or token limits.
6. **Artifact / Evidence selection** — prefer summaries; full content only if contract allows; rank by relevance + citation + recency.
7. **Tool descriptors** — only the names, descriptions and input schemas permitted by the binding.
8. **Final budget enforcement** — drop from lowest priority if still over budget (tools → artifacts → memory → step_context). Anchors and system are never dropped.
9. **Package** into provider message format and return messages + manifest.
10. **Persist** manifest on the TaskStep before model invocation.

### Rework Path

On verification failure the Orchestrator may re-invoke the same specialist. The assembler injects failure context into the anchors and may reduce memory/artifact budgets to leave room for the failure information.

### Guarantees

- Anchors always present.
- Total tokens ≤ contract limit.
- No full conversation history.
- Every included item recorded in the manifest.
- Deterministic for the same durable state (stable secondary sort recommended).

---

## 6. Runtime Interaction (Context Assembly + Specialist Invocation)

```
Orchestrator
  → Select next step + specialist; lookup contract + binding
  → Context Assembler
       ↔ Task Store          (Task + TaskStep)
       ↔ Capability Registry (policy + tool descriptors)
       ↔ Memory Manager      (hybrid retrieve)
       ↔ Artifact Store      (relevant summaries)
  ← messages + ContextManifest
  → Persist manifest on TaskStep
  → Specialist (model + allowed MCP tools only)
  ← typed output + evidence refs
  → Persist new Artifacts
  → Verifier
       → Pass  → next step / Synthesizer
       → Fail  → rework (re-assemble) or replan
```

**Key interaction rules**

- Context Assembler is the only component that builds model context.
- Specialist sees only the assembled context + typed input + allowed tools.
- Tool results from previous steps appear only as Artifacts or step summaries.
- Verifier operates on typed output + evidence against success_criteria.
- All side-effecting tools must write audit entries to the Event Log.

---

## 7. Hybrid Scoring for Memory / Artifact Retrieval

Ranking formula used inside Context Assembly:

```
hybrid_score = (semantic × 0.7) + (recency × 0.2) + (importance × 0.1)
```

All component scores are normalized to [0.0, 1.0] before weighting.

| Component   | Weight | Meaning |
|-------------|--------|---------|
| Semantic    | 0.7    | Cosine / inner-product similarity of embeddings. Dominant because relevance to the current step is primary. |
| Recency     | 0.2    | Time-decay (e.g. exponential). Gives a meaningful boost to fresh information without overriding relevance. |
| Importance  | 0.1    | Static or slowly changing signal (user pin, citation count, promotion flag, profile priority). Light prior / tie-breaker. |

**Rationale:** Relevance first, freshness second, protected knowledge third. Weights are configurable per specialist via `context_policy` if needed. Stable secondary sort (ID or timestamp) keeps ranking deterministic.

The same weighted approach can be reused for Artifacts (substituting citation_count or “linked_to_current_step” for importance).

---

## 8. Vector Store Normalization Guidelines

Normalization is required for stable semantic scores in the hybrid ranking.

### Definition

L2 (Euclidean) normalization scales every embedding to unit length:

```
v_norm = v / ||v||₂
```

Direction (semantics) is preserved; magnitude is discarded.

### Why It Matters

- Makes cosine similarity identical to simple dot product.
- Produces consistent, bounded scores for the semantic term (× 0.7).
- Enables faster inner-product indexes.
- Prevents magnitude bias and mixing of incompatible embedding sources.

### Recommended Atlas Practice

1. **Normalize on every write and every query.**
2. Prefer inner-product / dot-product metric once unit-length is guaranteed; otherwise use cosine (store handles normalization).
3. Never mix normalized and unnormalized vectors in the same collection.
4. Store metadata flags (`normalized: true`, embedding model identity) for future migrations.
5. Map raw similarity into a stable [0, 1] range if required by thresholds.
6. Enforce normalization inside the Vector Store interface so specialists and Memory Manager never see raw vectors.

Most modern embedding models already return approximately unit vectors, but Atlas code must still enforce the contract.

**Invariant:** The Vector Store layer always presents unit-length vectors and stable similarity scores to the Context Assembler.

---

## 9. Implementation Recommendations & Practical Next Steps

Aligning with the original diagram’s “Next Practical Steps”:

1. **Validate the topology with real workloads** — start with a narrow vertical (e.g. coding + research) before expanding specialists.
2. **Define specialist contracts & schemas** — implement the shared base schema and the five concrete contracts; version them.
3. **Implement Task State & Execution Engine** — durable Task / TaskStep / Artifact / Memory model with checkpoints and tool-budget accounting.
4. **Build the Context Assembler** as a pure function of (Task, Step, Contract, Memory/Artifact state) that always emits a ContextManifest.
5. **Stand up the Capability Registry** with tool bindings and dynamic enablement.
6. **Wire Tier-0 MCP servers** first (Filesystem, Git/GitHub, Fetch/Search, Memory, Terminal).
7. **Enforce Vector Store normalization** as an invariant of the Platform Services layer.
8. **Add evaluation harness & regression suite** — measure contract compliance (schema validity, evidence presence, budget adherence) as well as task outcome quality.
9. **Integrate domain capabilities** (e.g. Morning Workflow) as ordinary bounded capabilities, not as a competing core.
10. **Iterate with real tasks and metrics** — observe verification pass rates, rework frequency, context-token distribution, and evidence quality.

### Security & Observability Notes

- Action Executor requires dry-run / human-approval gates for irreversible operations.
- All tool invocations and context manifests should be queryable via the Event Log.
- Privacy level on contracts must be respected by Memory and Artifact filters.
- Prefer least-privilege credentials stored in Platform Services (Config & Secrets + Auth).

---

## 10. Open Considerations for the Implementation Team

- Exact hybrid-score weight tuning per specialist (start with 0.7/0.2/0.1).
- Whether Sequential Thinking or similar reflective tools are exposed as first-class capabilities or kept internal to the Planner/Verifier.
- Parallel specialist invocation and merge semantics (current design is sequential per step).
- Promotion policy from Task/Artifact results into long-term Memory (verification gate recommended).
- Human-in-the-loop escalation points beyond the Verifier.
- Concrete ContextManifest schema and storage format.
- Choice of concrete vector database (pgvector is a strong default if Postgres is already present) while keeping the normalization invariant independent of the backend.

---

## Document Control

This advisory consolidates the topology review, MCP prioritization, capability-registry contracts, context-assembly algorithm and detailed logic, runtime interaction sequence, hybrid scoring explanation, and vector-store normalization guidance produced in the design discussion of 18 August 2026.

It is intended as a living reference for implementation. Contracts, budgets, and weights should be treated as starting points and refined under the evaluation harness.

**End of Advisory Document**
