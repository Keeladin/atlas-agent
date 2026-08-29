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
- action occurrences, confirmations and evidence.

The state is explicit and queryable rather than reconstructed from prompt history.

### Done means

A restart does not erase the owner's work, configuration, policy or evidence, and the UI reads the same persistent truth used by execution.

Persistent Memory is structurally separate from Knowledge. Owner-turn capture is grounded in the authenticated owner turn and dispatches the same governed remember/update/retract operations available everywhere else. Purge is atomic inside `atlas-work.db` and states its application-level, non-forensic boundary explicitly.

## Responsibility 2 — Own governed work

Atlas can create durable Work consisting of explicit semantic capability steps. Work is not granted a frozen scalar authority level.

Each step is resolved against current runtime policy when it actually executes. If policy requires confirmation, the Work waits on the same durable action occurrence used by Chat and direct control surfaces.

### Done means

A policy change made after Work was created still governs the eventual side effect, and the evidence records the resulting action state.

## Responsibility 3 — Use the full connected capability surface

Atlas maintains one capability inventory containing native operations plus every tool discovered from enabled MCP/n8n servers.

Availability, capability and authority are separate facts. A connected tool may be visible and technically available while runtime policy is `NO` or `CONFIRM`.

### Done means

Connecting or refreshing an MCP server updates the live capability inventory without adding a parallel permission mechanism or requiring a code deployment for each tool.

## Responsibility 4 — Act under literal owner policy

Owner policy resolves an exact semantic operation on an exact normalized resource to one of `NO`, `YES` or `CONFIRM`.

No policy row resolves to `NO`. `CONFIRM` is durable, exact-payload bound and rechecked immediately before execution.

### Done means

Changing authority in Companion changes the running Atlas immediately. It does not require editing systemd, changing MCP credentials, altering provider scopes, restarting the service or redeploying code.

## Responsibility 5 — Preserve technical truth and evidence

Atlas records durable action occurrences, policy revisions, payload hashes, execution receipts and verification state.

Failed, blocked and uncertain are valid runtime outcomes. A provider response does not get promoted to verified completion merely because it sounds confident.

Host self-restart is a special verification case: dispatch may leave the predecessor unable to observe the successor, so the next process reconciles the durable occurrence using the systemd invocation identity.

### Done means

The UI and Chat can explain what happened from persisted runtime evidence rather than inventing a post-hoc story.

## Responsibility 6 — Remain useful across interfaces

Companion is the canonical owner interface today. Its primary surfaces are Chat, Work, Sources and Atlas control. Memory is a secondary durable-context surface reached from Sources.

The interface may evolve, but authority and durable state remain backend/runtime responsibilities rather than UI-local state.

### Done means

A different trusted interface could use the same runtime semantics without creating a second Atlas or a second policy engine.

## Implemented product boundary

The greenfield runtime currently owns:

- Chat and durable conversation state;
- Work and Cadence;
- Knowledge references/notes and first-class owner Memory with supersession, retraction, recall and governed purge;
- native file and host capabilities;
- provider configuration and model execution;
- generic MCP/n8n discovery and invocation;
- generic Streamable HTTP and stdio MCP capability providers;
- Discovery-driven Google Workspace capabilities for Gmail, Drive and Calendar;
- runtime owner policy and exact confirmation;
- evidence and verification state;
- Companion runtime control.

Morning is not an Atlas responsibility. It is a separate product, has no runtime package or database in this repository, and is not represented as a hard-coded Atlas capability.

## Deliberate non-goals

Atlas does not create autonomous named sub-agents, use service configuration as discretionary policy, grant authority merely because a provider advertises an operation, or preserve obsolete architecture solely for compatibility.

The current product also does not claim that every possible domain workflow is already implemented. New responsibilities should enter through capabilities and explicit external boundaries rather than by expanding Atlas's identity.

## Completion standard

A feature is not implemented merely because its UI exists or a model can describe it. It is implemented when runtime state, capability execution, policy, evidence, verification, tests and owner-facing truth agree.
