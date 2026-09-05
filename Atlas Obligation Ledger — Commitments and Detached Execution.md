# Atlas Obligation Ledger — Commitments and Detached Execution

Status: architecture specification. This document freezes the obligation and request/execution boundary before implementation. It describes required runtime truth, forbidden states, recovery semantics, migration treatment, and deletion criteria.

## 1. Purpose

Atlas needs a durable answer to one question that Work, Chat, Memory, Actions and Evidence do not own:

> What does Atlas currently owe the owner?

An obligation is a durable directed commitment from Atlas to the authenticated owner. Work is one mechanism for servicing an obligation; it is not the owner of the commitment. Chat routes presentation. Actions record consequential execution. Evidence supports resolution. None of those substitutes for the obligation itself.

Two boundaries follow:

1. **Obligation owns the commitment. Work services the commitment.**
2. **A request handler may establish durable intent and servicing machinery, but it never owns or initiates execution of a durable objective.**

These rules apply whether API and execution loops share a process or are operationally isolated into separate processes.

## 2. Core invariants

- Obligations are grounded in authenticated owner language and are never invented by Atlas.
- Obligations describe **what** is owed, never **how** to satisfy it. They contain no capability id, Work step, dependency graph, or execution sequence.
- Bindings are advisory; resolution is authoritative. Deleting every binding must not change any obligation status or resolution.
- Resolution is written from evidence, never from a Work status transition.
- A request handler never executes durable Work inline.
- No consequential dispatch may occur unless obligation intake for the owner turn is `complete`.
- A partially captured obligation set is not assumed to be a safe subset of the owner's request: an unmapped span may constrain, condition, prohibit, or reorder a mapped outcome.
- Obligation intake is a separate semantic model call from planning. Its input contains owner language plus only the minimum bounded conversational context required to interpret that language; it does not receive capability inventory, Work state, tool results, provider plumbing, or planning context.
- Successful intake of zero obligations is valid and distinct from intake failure.
- Communication obligations resolve only after a grounded owner-facing turn is verified and durably persisted.
- Supersession may only be caused by a later authenticated owner turn.
- Withdrawal is owner-only. Planning, Work, recovery, serviceability assessment and reconciliation cannot withdraw an obligation.
- Lapse is orthogonal to resolution. Passing a temporal bound never silently removes what Atlas owes.

## 3. Owner-turn intake state

Obligation intake state belongs to the authenticated owner turn because it describes how completely Atlas understood that utterance.

For new ledger-aware owner turns:

```text
intake_status = complete | partial | failed
intake_schema_version = 1
intake_attempts
intake_provider
intake_model
intake_error_code
unmapped_spans[]
turn_completed_at
response_handed_off_at
```

`complete` means the extracted obligation set passed grounding and coverage validation. It may contain zero obligations.

`partial` means at least one grounded obligation was captured but coverage validation identified owner language that could not safely be mapped. Consequence-free inspection may continue, but consequential execution is forbidden.

`failed` means Atlas has no trustworthy obligation enumeration for the turn. Planning and consequential execution do not proceed.
A greeting such as `hi` is therefore `complete` with an empty obligation set. It is not an intake failure.

Obligation extraction and planning have independent retry semantics and independent evidence. Obligation creation is idempotent for one owner turn and grounded span; a retry may rediscover the same commitment but may not create a duplicate.

## 4. Obligation record

Schema shape:

```text
obligation_id
owner_principal_id
conversation_id
owner_turn_id
grounding_excerpt
text
kind                  state_change | communication
status                open | resolved | withdrawn | superseded
resolution_kind       fulfilled | declined_policy | unserviceable | ... | null
resolution_ref        typed durable reference | null
revision
satisfiable_until     optional derived instant
lapsed_at             optional observed instant
temporal_grounding_excerpt
temporal_anchor_at
temporal_anchor_timezone
created_at
resolved_at
supersedes            optional obligation_id
```

`grounding_excerpt` must be a verbatim substring of the authenticated owner turn. Runtime validates it before insert. An obligation with invented grounding is invalid.

`kind` states what constitutes fulfilment, not how Atlas will service the request. `state_change` may be fulfilled by suitable action/observation evidence. `communication` requires a persisted grounded owner-facing turn.
`status = open` implies `resolution_kind IS NULL`, `resolution_ref IS NULL`, and `resolved_at IS NULL`.

`status = resolved` requires both `resolution_kind` and `resolution_ref`. `resolution_kind` records why the commitment ceased to be open; `resolution_ref` points at the authoritative evidence-bearing record.

`status = withdrawn` requires an explicit authenticated owner action or later grounded owner utterance as its authoritative reference. No model-initiated path may write it.

`status = superseded` requires a replacement obligation created from a later authenticated owner turn. The old row remains permanently auditable and its reference points at the replacement obligation.

`revision` increments on every owner-visible authoritative change to the obligation: status, resolution, lapse observation, or other durable state used in reporting. Advisory binding changes do not increment it.

## 5. Advisory servicing bindings

Work and other servicing mechanisms attach through a many-to-many relation such as:

```text
obligation_bindings
  obligation_id
  mechanism_kind       work_step | occurrence | other runtime mechanism
  mechanism_id
  created_at
```

Bindings answer only: **what mechanism currently appears to be servicing this obligation?**

They never answer whether the obligation is fulfilled. A Work item may be cancelled, revised, replaced, split or abandoned while the obligation remains unchanged. One Work step may service several obligations and one obligation may be serviced by several mechanisms over its lifetime.

Deleting all bindings is permitted to damage observability of current servicing, but it must produce zero changes to obligation status, resolution, lapse, or historical evidence.

## 6. Temporal satisfiability

When owner language contains a temporal bound, intake may extract the semantic temporal expression, but runtime owns normalization to an absolute instant.
The durable temporal record preserves both source and normalization basis:

```text
temporal_grounding_excerpt
satisfiable_until
temporal_anchor_at
temporal_anchor_timezone
```

The derived bound is immutable on that obligation. If the owner later changes the deadline or timing, Atlas creates a new grounded obligation from that later turn and supersedes the old one. Historical meaning is never silently retimed.

`lapsed_at` is stamped by the background reconciliation loop when Atlas observes that an open obligation has passed `satisfiable_until`. It is not computed opportunistically on read.

A lapsed obligation remains `open`. Lapse means Atlas still owes the owner something but the originally requested outcome is no longer satisfiable as stated. Attention and reporting may distinguish pending from lapsed without erasing the commitment.

## 7. Planning and the staged execution boundary

After successful obligation intake, planning may choose mechanisms to service the commitments. Planning may compose Work, but the request-serving path may only persist that Work in a staged state. It may not execute it inline.

The handoff facts belong to the owner turn:

```text
turn_completed_at
response_handed_off_at
```

`turn_completed_at` means Atlas finished the semantic turn: obligation intake is durable, planning for the turn has reached a terminal response state, staged servicing machinery has been persisted, and the assistant response is durably stored.

`response_handed_off_at` means the final ASGI `http.response.body` has been successfully handed to the server transport. It does **not** claim that the remote client received or rendered the bytes.

The request handler never changes staged Work to runnable. The background execution loop derives runnability from durable turn facts and makes the transition itself.

A staged Work item derived from an owner turn is runnable only when the owner turn is ledger-aware, intake is `complete`, the Work has at least one backing open obligation, `turn_completed_at` is set, and `response_handed_off_at` is set.
### Handoff stamp location and failure semantics

The handoff stamp is written by ASGI middleware around the response `send` callable, after the awaited send of the final body frame returns. It is outside endpoint business logic and outside any request transaction that created obligations or staged Work.

The stamp write uses the normal SQLite connection discipline and busy timeout, but **there is no secondary derivation that infers handoff**. A missing stamp is never repaired from an assistant turn, Work state, elapsed time, or restart evidence.

If the stamp write fails after the transport handoff:

- log the failure at error/critical severity with conversation and owner-turn identity;
- do not make staged Work runnable;
- the execution/recovery loop treats the durable `turn_completed_at != null AND response_handed_off_at == null` state as `handoff_unconfirmed`;
- associated staged Work moves to or remains `waiting`;
- backing obligations remain open and Attention surfaces the ambiguity.

The failure is therefore loud without creating an alternate path around the handoff invariant. A later reader may know the response probably left Atlas, but runtime may not upgrade that probability into execution authority.

## 8. Deterministic staged-Work recovery

Recovery derives state from the originating owner turn, not from Work metadata alone:

```text
intake complete + turn complete + response handed off + backing open obligations
    -> eligible for runnable transition

intake complete + turn complete + response handoff unconfirmed
    -> waiting; obligations remain open; Attention surfaces handoff ambiguity

intake partial or failed
    -> never runnable; obligations remain open where present

staged Work with no backing obligation
    -> invalid runtime state; never runnable
```

An assistant turn by itself does not prove response handoff. A deliberate self-restart receives no special bypass. Under correct new-runtime behaviour, self-restart cannot be dispatched before the execution loop has derived runnability from a confirmed handoff.

Legacy Work created before this specification is handled by the migration rule in section 17 rather than being mistaken for ledger-aware staged Work.
## 9. Resolution from evidence

Work completion is not obligation completion. A bound Work step reaching `completed` is only one possible source of evidence.

The background reconciliation loop evaluates open obligations against authoritative evidence. Resolution is a separate write with its own compare-and-set revision check.

For `state_change` obligations, suitable verified action or observation evidence may be sufficient for `status = resolved`, `resolution_kind = fulfilled`.

For `communication` obligations, obtaining the requested information is supporting evidence only. Fulfilment requires a grounded owner-facing Chat turn that actually communicates the requested result.

Policy refusal resolves distinctly:

```text
status = resolved
resolution_kind = declined_policy
resolution_ref = policy/action evidence reference
```

Work cancellation never resolves or withdraws a bound obligation. The mechanism stops; the obligation remains open unless another authoritative owner or evidence event changes it.

## 10. Communication obligations and reporting order

`reportable` is a computed predicate, never a durable column. Each reconciliation/reporting pass derives it from the current open communication obligations and current supporting evidence.

The required ordering is:

1. Reconcile everything resolvable from action/observation evidence alone.
2. Compute the set of still-open communication obligations for which sufficient supporting evidence currently exists.
3. Generate owner-facing prose covering exactly that computed reportable set, plus an explicit statement of obligations that remain open and unreportable.
4. Verify that every factual claim in the candidate prose is supported by the supplied durable evidence.
5. Re-check the report snapshot against current authoritative state.
6. Persist the verified owner-facing turn.
7. Only after successful persistence resolve the communication obligations it actually fulfilled, with `resolution_ref = chat_turn:<turn_id>`.

If report generation or verification fails, the communication obligations stay open. A deterministic fallback that merely points the owner to recorded Work evidence does not fulfil an obligation such as `tell me the weather`.
### Fixed report snapshot

A report is generated against a fixed snapshot, not against a moving query result. The snapshot contains:

- each included obligation id and obligation revision;
- the exact relevant evidence identities/digests used to establish reportability and grounding;
- the set of open obligations represented as outstanding/unreportable in the candidate.

Immediately before persistence, runtime re-reads every obligation revision and revalidates the relevant evidence basis. If any obligation revision changed, an evidence record was invalidated/replaced, or the computed reportable/outstanding sets no longer match the snapshot, the candidate is discarded and regenerated.

This is the reporting equivalent of `work.revise`'s base-revision check. A report may never land describing an obligation as outstanding after authoritative state has already moved, nor claim a result from evidence that is no longer the basis of the current reconciliation view.

Persisting the report and resolving the communication obligations should use one database transaction where those records share a database. Where they do not share a transaction boundary, the persisted Chat turn remains the first authoritative event and reconciliation may idempotently resolve the obligations from that turn afterwards; it may never resolve them before the turn exists.

## 11. Serviceability and `unserviceable`

`unserviceable` is a resolution about Atlas's capability configuration at a point in time, not a timeless fact.

Atlas may resolve an obligation as unserviceable only after:

1. a durable serviceability assessment finds no viable route;
2. that assessment records the exact capability-registry basis used;
3. a grounded owner-facing explanation is persisted.

The resolution then records:

```text
status = resolved
resolution_kind = unserviceable
resolution_ref = serviceability_assessment:<id> + owner-facing turn reference
```

The serviceability assessment records the canonical capability-registry fingerprint, capability-search basis, and assessment timestamp. The fingerprint should use the same definition/schema identity already used to fingerprint Atlas's live capability retrieval representation rather than inventing a second registry identity.
A later registry change does not automatically reopen the obligation. Automatic reopening would silently resurrect a commitment the owner may have moved on from.

Instead Atlas exposes a derived view:

> obligations resolved `unserviceable` whose recorded registry fingerprint differs from the current registry fingerprint

That view may surface: `you asked for this, I could not do it then, and Atlas's capabilities have changed.` Renewed responsibility requires a new owner turn and, where appropriate, a new obligation that supersedes the historical one.

## 12. Supersession and withdrawal

Supersession is owner-grounded history, not an editing tool for the planner.

An obligation may only be superseded by an obligation created from a **later authenticated owner turn** for the same owner principal. A same-turn obligation may not supersede another same-turn obligation. A planner, reconciler or Work revision may not create supersession merely to make completeness arithmetic cleaner.

Withdrawal is stricter:

- only an explicit owner action or a later authenticated owner utterance whose grounding supports withdrawal may write `status = withdrawn`;
- Work cancellation does not withdraw;
- planner output does not withdraw;
- recovery does not withdraw;
- policy refusal does not withdraw;
- `unserviceable` assessment does not withdraw.

The withdrawal row carries the authoritative owner action/turn reference. Nothing model-initiated may write the state.

## 13. Completeness

Completeness is arithmetic over durable obligations, not a model judgement.

For a ledger-aware owner turn:

```sql
SELECT obligation_id
FROM obligations
WHERE owner_turn_id = ?
  AND status = 'open';
```

Zero rows means that turn has no outstanding commitments. Any remaining row prevents Atlas from presenting the owner objective as wholly complete.
There is no historical/pre-ledger completeness branch. Section 17 requires a clean development-state reset when this architecture lands, so every retained owner turn is governed by the obligation-intake schema.

## 14. Attention and dangling commitments

`Needs you` is derived from obligation truth rather than Work-status heuristics. Useful derived conditions include:

- open obligation with no active servicing binding;
- open obligation whose servicing mechanism is blocked or waiting on owner input;
- open obligation with `lapsed_at` set;
- open obligation from a complete turn whose response handoff is unconfirmed;
- historical `unserviceable` resolution whose registry basis is stale.

These are views over durable facts. They are not additional obligation states.

## 15. Forbidden states

The following states are invalid and must be prevented by schema constraints, transactional checks, compare-and-set transitions, or explicit runtime assertions. They are intended to be directly testable against code and persisted state.

- staged ledger-aware Work with zero backing obligations;
- runnable ledger-aware Work whose owner turn is not `complete`;
- runnable ledger-aware Work with null `turn_completed_at` or null `response_handed_off_at`;
- consequential occurrence for an owner turn whose intake is `partial`, `failed`, or absent under the ledger-aware schema;
- obligation whose `grounding_excerpt` is not a substring of its authenticated owner turn;
- obligation containing a capability id, execution dependency, ordering edge, or Work-step definition;
- `status = open` with a non-null resolution kind/ref/resolved timestamp;
- `status = resolved` with null `resolution_kind` or null `resolution_ref`;
- communication obligation resolved as `fulfilled` without `resolution_ref` identifying a verified persisted Chat turn;
- state-change obligation resolved as `fulfilled` solely because bound Work changed status, without supporting execution/observation evidence;
- withdrawn obligation without an explicit owner action or later grounded owner-turn reference;
- withdrawal written by planning, Work cancellation, recovery, policy resolution, serviceability assessment, or generic reconciliation;
- `lapsed_at` set while the obligation is not `open`; lapse history for a later-resolved obligation belongs in evidence/event history, not as current satisfiability state;
- a new obligation's `supersedes` pointer targeting an obligation from the same or a later owner turn;
- automatic reopening of a resolved `unserviceable` obligation after registry change;
- persisted `reportable` readiness state;
- communication resolution occurring before the fulfilling Chat turn is durably persisted;
- report persistence against stale obligation revisions or stale evidence basis;
- inference of `response_handed_off_at` from elapsed time, assistant-turn existence, Work state, or restart evidence.
## 16. What this specification deletes or demotes

The migration is complete only when responsibility semantics move out of Work and Chat rather than being duplicated beside the ledger.

Delete:

- Work-level judgement about whether the owner's objective is complete;
- completeness instructions from the Work completion-report prompt;
- residual-request/residual-obligation bookkeeping inside Chat or Work;
- `Needs you` heuristics that infer owner commitments from Work status alone;
- restart-specific Chat logic whose purpose is to preserve an objective that should instead be represented by obligations and detached execution.

Retain but demote:

- `chat_origin` remains a routing/presentation pointer identifying where Work state and completion reporting should appear. It no longer means that Work owns the originating responsibility.
- Work completion remains durable execution truth about its own steps and evidence. It does not assert that all owner obligations were satisfied.
- generic transport reconnect/polling may remain as UI resilience, but it reads durable turn/obligation/Work truth and carries no restart-specific responsibility semantics.

The deletion criterion is deliberate: if Work-level completeness guessing, residual-request machinery, or restart-specific ownership handling remains necessary after migration, the boundary has not actually moved.

## 17. Development schema cutover and state reset

Atlas is still in active development. Existing SQLite runtime entries are disposable development state and must not constrain the obligation architecture.

When this specification is implemented, Atlas uses a **clean schema/state reset**, not a compatibility migration. The implementation may recreate the development SQLite databases from the current canonical schema rather than preserving pre-ledger rows.

Do not:

- backfill obligations from historical Work objectives or Chat turns;
- introduce `legacy_untracked` markers;
- add ledger cutover exemptions for historical Work;
- support mixed pre-ledger/post-ledger completeness semantics;
- retain compatibility branches whose only purpose is preserving disposable development rows.

Persistent source files, managed files that are deliberately re-enrolled, and encrypted secret material are outside this database-retention decision. They may be reattached or reconfigured through the current runtime contracts after the reset; old database identities are not authoritative merely because they existed before the reset.

After reset, every retained owner turn is ledger-era state. Every owner turn therefore has explicit obligation-intake state, and every staged Work item that services an owner request must satisfy the normal backing-obligation invariant. There is no historical exemption path.

The reset is part of the architectural migration: obsolete database state is discarded so the live runtime has one coherent set of semantics rather than compatibility archaeology.
## 18. Required acceptance tests

The ledger is load-bearing only if these behaviours are proven end to end:

1. A three-outcome owner turn produces three grounded obligations, committed before the first consequential dispatch.
2. An obligation whose grounding excerpt is not a substring of the authenticated owner turn is rejected.
3. `hi` produces `intake_status = complete`, zero obligations, and no intake failure.
4. Intake failure while planning would otherwise succeed produces zero obligations, zero consequential occurrences, and a truthful intake-failure turn.
5. Partial intake persists captured obligations and unmapped spans but produces zero consequential occurrences.
6. `SIGKILL` after obligation commit and before first dispatch leaves obligations present and zero occurrences.
7. Work completion with one obligation still open cannot yield a wholly-complete owner report; completeness is derived without asking the verifier.
8. Policy `NO` resolves the relevant obligation as `declined_policy` with authoritative policy/action evidence rather than silence.
9. Work cancellation leaves bound obligations open and visible in Attention.
10. An obligation with no servicing binding survives restart and remains visible as a dangling commitment.
11. A communication obligation with supporting evidence remains open until a verified grounded Chat turn is durably persisted.
12. Failed report verification leaves the communication obligation open even when supporting evidence exists.
13. Report generation is discarded when any snapshotted obligation revision or relevant evidence basis changes before persistence.
14. Staged Work cannot run before `response_handed_off_at`; the request handler never performs the runnable transition.
15. Failure to persist `response_handed_off_at` is logged loudly, leaves Work waiting, and surfaces handoff ambiguity without a fallback release path.
16. Recovery deterministically distinguishes released, handoff-unconfirmed, partial/failed-intake, and invalid-unbacked Work; there is no legacy exemption path.
17. A lapsed open obligation receives a durable `lapsed_at` observation; resolving it removes the live lapse annotation while preserving lapse event history.
18. A later owner turn may supersede an earlier obligation; same-turn or backward-invalid supersession is rejected.
19. Registry change identifies stale `unserviceable` assessments without reopening the resolved obligation.
20. Withdrawal can be written only by an explicit authenticated owner action or grounded later owner utterance. Work cancellation, planner output, recovery, policy refusal and unserviceable resolution are each asserted unable to write `withdrawn`.
21. Deleting every obligation binding leaves obligation status and resolution unchanged.
22. A clean development-state reset leaves no pre-ledger Chat/Work rows and no `legacy_untracked` or cutover-exemption path; every newly retained owner turn carries explicit obligation-intake state.

## 19. Flagship restart test under this model

For `Restart your API service, then verify that it came back healthy. Then tell me what the weather is like today in Cullinan.` the expected semantic outcome is:

- intake commits three independently assertable obligations before planning;
- planning may compose one staged Work route with restart, verification and weather-supporting retrieval in the required execution order;
- the HTTP request completes and records response handoff before the execution loop may claim that Work;
- restart occurs with no owner request executing inline;
- restart and health obligations may resolve from verified action/observation evidence;
- weather remains an open communication obligation until a verified owner-facing turn actually reports the weather;
- if reporting fails, Atlas remains online and the weather obligation remains visibly open rather than being cosmetically declared complete.

No second owner message is required on the healthy path. A transport failure is presentation evidence, never execution truth.
