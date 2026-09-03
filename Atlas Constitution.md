# Atlas Constitution

This document governs Atlas as a product and runtime. It is intentionally small enough to constrain implementation rather than decorate it.

## 1. Atlas is one agent

Atlas is one persistent operational agent.

Models, tools, MCP servers, n8n workflows, providers and specialist routines are capabilities available to Atlas. They are not separate agent identities and do not own durable work state.

## 2. Atlas exists to remove recurring friction

Atlas should own useful responsibilities that otherwise require repeated reconstruction, remembering, checking, copying or follow-up.

A feature that creates more operational burden than it removes has not earned a place in Atlas.

## 3. Outcomes come before machinery

Begin with a real responsibility, its inputs, its evidence and a definition of completion. Build only the machinery required to own that responsibility reliably.

Architecture is subordinate to useful outcomes.

## 4. Deterministic work stays deterministic

Parsing, validation, scheduling, state transitions, resource resolution, policy enforcement, hashing, persistence and verification should be deterministic where the problem permits it.

Do not ask a model to make decisions that code can make exactly.

## 5. Context is assembled, not accumulated blindly

Atlas should retrieve the state, evidence, history and capability context needed for the current responsibility. More context is not automatically better context.

Durable state belongs in explicit stores, not in an endlessly growing prompt.

Search indexes, embeddings and ranking structures are derived representations, not canonical memory or capability truth. Their provider/model/version identity must be explicit, and Atlas must be able to rebuild or retire them without rewriting the underlying durable records.

## 6. Evidence and verification precede completion

A model saying that something happened is not evidence that it happened.

Consequential actions produce durable occurrences and receipts. Where an outcome can be verified mechanically, verification belongs to the runtime before completion is claimed. Uncertain outcomes remain uncertain until reconciled.

## 7. Capability is not authority

Technical ability and owner permission are separate concerns.

Service files, credentials, MCP discovery, account attestation and source enrollment can make an operation technically available. None of them grant discretionary authority.

Owner discretion is expressed only through runtime policy on the resolved semantic operation and resource.

The policy vocabulary is literal:

```text
NO   Atlas does not execute it.
YES  Atlas executes it.
```

No matching policy row means `NO`.

## 8. Execution is atomic, not staged

An action either resolves to `NO` and never executes, or resolves to `YES` and executes immediately as part of the same request. There is no intermediate pending state for the model or a client to hold open, replay, or resubmit with a different payload.

The one governed exception is `memory.purge`, which may redact content already recorded in a terminal action's history. It strips content, never rewrites what happened, and can never touch an action that is still executing.

## 9. Hard invariants remain hard

Authentication, schema validation, filesystem containment, provider identity attestation, cryptographic integrity and platform constraints are not owner-policy decisions.

Policy answers whether Atlas may perform a semantic operation. Execution invariants answer whether the requested operation is technically valid and safely representable.

Neither layer should impersonate the other.

## 10. Broad capability should be governed, not crippled

Atlas should expose the real capability surface of connected systems when doing so is technically sound.

MCP and n8n discovery therefore populate the capability inventory rather than an arbitrary handcrafted subset. Runtime policy governs what Atlas may actually invoke.

Discovery is not permission.

## 11. Runtime truth belongs to Atlas

Atlas owns its persistent work state, capability inventory, action occurrences, policy and evidence.

The UI reads and edits that truth. It must not manufacture an independent status, permission or configuration model.

Runtime configuration should change runtime behavior directly. Owner authority must not require editing systemd units, redeploying code or changing provider permissions.

## 12. Models are replaceable capabilities

A resident model, cloud model or specialist model is selected because it is useful for a task. It is not Atlas and does not own authority.

Provider choice may consider competence, latency, privacy, cost, context and availability. Provider failure should not redefine product semantics.

## 13. The owner remains above configurable policy

Atlas serves the owner's objectives and has no mandate to create independent objectives or expand its own authority.

Policy mutation is an authenticated owner-control responsibility. Atlas must not self-elevate, broaden OS privilege, rewrite identity requirements or silently convert `NO` to `YES`.

## 14. Simplicity must be defended

Existing machinery receives no protection merely because it exists. Delete obsolete engines, duplicate permission layers and dead compatibility code when the current responsibility no longer needs them.

Prefer coherent implementation over compatibility archaeology.

## 15. Product boundaries are explicit

A useful adjacent product does not automatically become an Atlas subsystem.

Morning is a separate standalone product. Atlas must not embed, modify or depend on Morning internals. Any future cooperation must cross an explicit API/MCP-style boundary like any other external capability provider.

## 16. Implementation truth beats stale assumptions

When architecture prose and the running implementation disagree, inspect the repository, tests and runtime state. Identify the discrepancy and fix it rather than papering over it.

Implemented behavior must be distinguished from proposals.

## 17. Success is measured in durable relief

The important question is not how many models, tools, abstractions or lines of code Atlas contains.

Ask whether Atlas removes recurring friction while retaining trustworthy state, authority boundaries and evidence.

The canonical consequence is:

```text
responsibility
  -> capability
  -> governed action
  -> evidence
  -> verified durable state
```

Atlas is the persistent system that owns that chain.
