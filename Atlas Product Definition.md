# Atlas Product Definition

## Product promise

Atlas is a persistent operational companion that can remember useful context, own durable work, use connected capabilities and act under explicit owner authority.

It is not a collection of autonomous agents and it is not a chat wrapper around one model.

## Responsibility 1 — Maintain durable operational state

Atlas persists the state that matters across sessions:

- conversations;
- Work and Work steps;
- recurring Cadence duties;
- Knowledge references/notes and first-class persistent Memory;
- external account identity/bindings;
- provider and MCP configuration;
- source enrollment;
- owner policy;
- action occurrences and evidence.

The state is explicit and queryable rather than reconstructed from prompt history.

### Done means

A restart does not erase the owner's work, configuration, policy or evidence, and the UI reads the same persistent truth used by execution.

Persistent Memory is structurally separate from Knowledge. Current Memory V2 keeps canonical memory rows separate from derived lexical/vector retrieval state, fuses FTS5 and local semantic-vector rankings, and versions each vector generation by its embedding provider/model snapshot/package/dimensions/normalization contract. Owner-turn capture is grounded in the authenticated owner turn and dispatches the same governed remember/update/retract operations available everywhere else. Purge is atomic inside `atlas-work.db` and states its application-level, non-forensic boundary explicitly. The older `memory_items` table is preserved but is not queried or automatically migrated by Memory V2; migration of that historical data is an explicit operation, not an implicit startup side effect.

## Responsibility 2 — Own governed work

Atlas can create durable Work consisting of explicit semantic capability steps. Work is not granted a frozen scalar authority level.

Each step is resolved against current runtime policy when it actually executes. If the resulting outcome is unverified, the Work waits on the same durable `uncertain` action occurrence used by Chat and direct control surfaces until it is reconciled.

Chat authors and interprets ordinary Work and recurring `work_template` Cadence through governed capabilities; Cadence remains durable recurring intent and Work remains durable execution truth. Monitored `intake_sweep` duties are readable and runnable but are not conversationally authored or edited in this iteration. Links from Work/Cadence can focus one inference on an exact runtime identity, while the persisted reference remains historical provenance rather than sticky conversational state.

The model proposes when an objective deserves durable ownership, but it is not the sole continuity boundary. After resource resolution, `CapabilityRuntime` refuses an action that cannot safely remain owned by an ephemeral caller unless a real `work_id` is present. Self-restart is the canonical example: Atlas may discuss or inspect it in Chat, but the restart itself must belong to Work before dispatch. Runtime service identity must itself be grounded: configured and cgroup/MainPID-derived identity may agree, otherwise Atlas fails closed. An already-dispatched `uncertain` action is never replayed; identical in-turn retries are deterministically refused, and if an obligation remains, Work may adopt that exact occurrence after deterministic identity checks. Terminal Chat-origin Work reports through one deduplicated completion path whether completion occurred normally or during recovery.

Ordered obligations inherit durability across that boundary: once one step requires durable ownership, any still-unsatisfied composable suffix remains part of the same responsibility instead of being forgotten when the runtime restarts. Planner failure itself is observable state: Atlas retries one unusable `chat.turn` result, records compact provider diagnostics on the turn, and reports `planner_unavailable` rather than fabricating a normal reply.

### Done means

A policy change made after Work was created still governs the eventual side effect, and the evidence records the resulting action state.

## Responsibility 3 — Use the full connected capability surface

Atlas maintains one capability inventory containing native operations plus every tool discovered from enabled MCP/n8n servers.

Availability, capability and authority are separate facts. A connected tool may be visible and technically available while runtime policy is `NO`.

### Done means

Connecting or refreshing an MCP server updates both the live capability inventory and the Companion capability browser without adding a parallel permission mechanism or requiring a code deployment for each tool. Discovered input schemas are rendered generically, with raw JSON as the fallback rather than provider-specific UI code. Chat does not receive the whole schema inventory on every turn: it builds a semantic shortlist over live registry truth using exact/sparse matching plus local dense embeddings fused by reciprocal-rank fusion, while the complete compact capability-family catalog remains available for wider search.

## Responsibility 4 — Act under literal owner policy

Owner policy resolves an exact semantic operation on an exact normalized resource to `NO` or `YES`.

No matching policy row resolves to `NO`. Resolution and execution happen within the same request — there is no separate pending state to reopen or replay.

### Done means

Changing authority in Companion changes the running Atlas immediately. It does not require editing systemd, changing MCP credentials, altering provider scopes, restarting the service or redeploying code.

## Responsibility 5 — Preserve technical truth and evidence

Atlas records durable action occurrences, policy revisions, payload hashes, execution receipts and verification state.

Failed, blocked and uncertain are valid runtime outcomes. A provider response does not get promoted to verified completion merely because it sounds confident.

Host self-restart is a special verification case: dispatch may leave the predecessor unable to observe the successor, so the next process reconciles the durable occurrence using the systemd invocation identity.

### Done means

The UI and Chat can explain what happened from persisted runtime evidence rather than inventing a post-hoc story. Successful conversational Work/Cadence mutations persist their presentable action occurrence on the assistant turn; scheduled Cadence runs remain visible through Cadence history and their materialized Work rather than being mislabeled as owner-triggered run cards.

## Responsibility 6 — Remain useful across interfaces

Companion is the canonical owner interface today. Its default owner surface interweaves conversation, active Work, Cadence, attention and runtime activity around the current objective. Work, Sources and Atlas Control remain deeper operational and technical views rather than competing primary products. Memory is a secondary durable-context surface reached from the owner surface or Sources.

The interface may evolve, but authority and durable state remain backend/runtime responsibilities rather than UI-local state.

### Done means

A different trusted interface could use the same runtime semantics without creating a second Atlas or a second policy engine.

## Implemented product boundary

The greenfield runtime currently owns:

- Chat and durable conversation state;
- Work and Cadence;
- Knowledge references/notes and first-class owner Memory with supersession, retraction, recall and governed purge;
- native file and host capabilities, including exact Debian package inspection and broker-mediated package mutation;
- provider-neutral public-web search, read, fetch, extraction, governed download, bounded same-origin crawl, and read-only JavaScript rendering, with deterministic translation of provider-native streams into versioned `data_only` evidence plus provenance before reasoning;
- model and web-provider configuration, encrypted credential custody, model execution, and local embedding retrieval for Memory/capability discovery;
- generic MCP/n8n discovery and invocation;
- generic Streamable HTTP and stdio MCP capability providers, including a narrow Termux/SSH Android-phone provider for location, telephony inspection and SMS;
- Discovery-driven Google Workspace capabilities for Gmail, Drive and Calendar;
- runtime owner policy;
- evidence and verification state;
- Companion runtime control.

Morning is not an Atlas responsibility. It is a separate product, has no runtime package or database in this repository, and is not represented as a hard-coded Atlas capability.

## Deliberate non-goals

Atlas does not create autonomous named sub-agents, use service configuration as discretionary policy, grant authority merely because a provider advertises an operation, or preserve obsolete architecture solely for compatibility.

The current product also does not claim that every possible domain workflow is already implemented. Read-only rendered browser execution is implemented; web form submission, authenticated interaction and other browser mutations remain unavailable until they have explicit capability contracts and runtime-policy semantics. Dense retrieval is implemented for Memory V2 and capability discovery, but Knowledge/OEM passage retrieval is still lexical FTS today; the product must not imply that manual retrieval has already been vectorized. New responsibilities should enter through capabilities and explicit external boundaries rather than by expanding Atlas's identity.

## Completion standard

A feature is not implemented merely because its UI exists or a model can describe it. It is implemented when runtime state, capability execution, policy, evidence, verification, tests and owner-facing truth agree.
