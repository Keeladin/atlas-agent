# Atlas Architecture — Runtime and Topology

This document describes the greenfield Atlas implementation. It is descriptive of the current runtime, not a compatibility map for archived Atlas versions.

## 1. Canonical runtime chain

Every consequential action crosses the same boundary:

```text
Intent / Chat / Work / Control
        |
        v
Capability
        |
        v
Schema validation
        |
        v
Deterministic scope + payload normalization
        |
        v
OwnerPolicy.resolve(principal, scope, operation)
        |
        +---- NO ------> durable blocked occurrence
        +---- CONFIRM -> durable exact confirmation
        +---- YES -----> execution
                         |
                         v
                  technical invariants
                         |
                         v
                    provider / OS
                         |
                         v
                  evidence + verification
```

The model can select capabilities and construct arguments. It cannot authorize the resulting action.

## 2. Composition root

`atlas_api.compose.build_runtime(instance_root)` constructs the single Atlas runtime from persistent stores and capability providers.

The composition order is intentional:

1. identity, policy, action and evidence stores;
2. capability registry and canonical action gate;
3. encrypted credentials and provider settings;
4. MCP/n8n discovery;
5. enrolled local sources and host capabilities;
6. Knowledge, first-class Memory, Work and Cadence;
7. Chat over the resulting live capability inventory and separate Knowledge/Memory recall stores.

`atlas_api` exposes the authenticated HTTP control plane and serves the built Companion PWA. It is not a second engine.

## 3. Identity and provenance

The identity store contains:

- principals;
- provider account connections;
- technical service bindings;
- provider settings;
- MCP server configuration;
- source-root enrollment;
- owner-policy events.

Account connections state whose external account is connected. Service bindings state how a provider can technically dispatch operations and which operations were attested. Neither grants owner permission.

Invocation provenance records the principal kind and trusted surface that initiated an action. The owner policy is resolved for that principal after resource normalization.

## 4. Owner policy

`PolicyStore` is append-only. Each event records principal, normalized scope, operation, decision, reason and sequence.

`OwnerPolicy` resolves the latest rule for each `(principal, scope, operation)` and then chooses the most specific matching scope. An exact operation outranks a wildcard at equal scope specificity. No candidate resolves to `NO`.

Policy has only three values: `NO`, `YES`, `CONFIRM`.

Sensitive-child restrictions are ordinary visible policy rows. There is no invisible discretionary deny floor beneath owner policy.

Policy mutation is available only through authenticated mutation-control routes. Capabilities cannot call a policy-write capability because none exists.

## 5. Actions and confirmation

`ActionOccurrence` is the durable unit of governed execution. It records:

- capability id and semantic operation;
- normalized resource scope;
- normalized payload and SHA-256 digest;
- principal and surface provenance;
- policy decision, revision and matched event;
- action state;
- Work/step linkage where applicable;
- result, receipt and error fields;
- created, confirmed, executed and completed timestamps.

`CONFIRM` creates a `pending_confirmation` occurrence. Confirmation is accepted only from the same authenticated principal and expires after the configured confirmation window.

Immediately before execution, policy is resolved again:

- current `NO` -> blocked with `policy_revoked_before_execution`;
- current `CONFIRM` -> the exact confirmation satisfies this occurrence;
- current `YES` -> execution proceeds.

The model and clients cannot bypass this by supplying a confirmation flag.

The confirm→execute payload is immutable. The one deliberately separate terminal-history mutation is governed `memory.purge` redaction: it can only strip content from non-executing memory occurrences, never introduces executable values, never recomputes `payload_sha256`, and its SQL guard excludes `pending_confirmation` and `executing`. `ActionStore.transition()` remains closed to payload updates.

## 6. Capability registry

`CapabilityRegistry` contains the complete runtime inventory. A registration consists of:

- a semantic capability definition;
- JSON input schema;
- deterministic scope resolver;
- executor;
- technical availability function;
- metadata such as provider, source or scope hint.

Definitions classify effect (`none`, `internal`, `reversible`, `external`, `destructive`) but do not encode owner permission.

`CapabilityRuntime.invoke()` validates input, resolves the exact scope and normalized payload, and submits the attested request to `ActionRuntime`. If an executor needs trusted owner context, `ActionRuntime` binds the occurrence principal only when resolving the executor; that context is not added to the stored or hashed payload.

`CapabilityDefinition.input_schema` is also owner-facing runtime truth. Companion may render controls from it, but the backend validates the submitted payload again before scope resolution. A generated form therefore improves usability without becoming an enforcement boundary.

## 7. MCP and n8n

MCP servers are runtime-managed persistent connections. Enabling or refreshing a server discovers every advertised tool and registers it under the server's capability namespace. Atlas supports both Streamable HTTP MCP and locally spawned stdio MCP providers. Transport changes connection mechanics, not policy semantics.

n8n uses the same generic MCP mechanism. A server being of kind `n8n` changes inventory metadata, not authority semantics. Production connects to n8n's built-in instance MCP server (`/mcp-server/http`) as the runtime-managed `n8n-runtime` provider. Atlas stores its bearer credential only in `CredentialStore` and registers every tool the n8n server advertises; there is no mail-specific or workflow-specific Atlas wrapper layer.

The pre-v3 mail-only n8n MCP registration is not part of the live Atlas inventory. Its legacy n8n workflows were unpublished during cutover, while n8n itself remains an independent automation service that Atlas can operate through the generic MCP boundary.

Each discovered raw tool resolves to a scope shaped like:

```text
mcp/<server-id>/tool/<advertised-tool-name>
```

The technical transport can call the remote tool only after Atlas has crossed the policy gate. Discovery and credentials never imply authority.

## 8. External capability providers

Provider-specific functionality should enter Atlas through the generic capability boundary rather than by creating parallel domain runtimes. A provider may translate an external system's discovery/schema surface into MCP tools, but it does not define owner authority.

The implemented Google Workspace provider is the first example. It reads Google's Discovery Service for enabled Workspace APIs and exposes the resulting Gmail, Drive and Calendar methods as ordinary MCP tools over stdio. Current `gws` supplies Google authentication and API execution. Its OAuth/config state is provider custody under the external production-state tree; this technical credential state does not grant Atlas discretionary authority. Atlas does not maintain a separate semantic mail layer or a handcrafted list of allowed Gmail functions.

The governing path remains:

```text
Google Discovery / provider tool
        -> MCP capability
        -> exact mcp/<server>/tool/<tool> scope
        -> NO | YES | CONFIRM
        -> provider execution
```

If Google exposes a method and the connected account is technically authorized for it, Atlas can inventory that method. Whether Atlas may invoke it is an owner-policy decision.

## 9. Local filesystem

Source enrollment registers a canonical host directory with the hardened local-source kernel. Enrollment exposes technical read and mutation operations; it does not encode owner authority.

The kernel uses descriptor-relative operations, no-follow path handling, mount containment, regular-file checks, no-overwrite mutations and managed quarantine for deletion.

Policy scopes are rooted at:

```text
files/<provider-namespace>/<root-id>[/<canonical-relative-path>]
```

The destination of copy/move/rename/restore is normalized into the governed payload before hashing and confirmation.

## 10. Host runtime

Host observation includes status, resources, storage and bounded filesystem inspection. Host filesystem paths are canonicalized before policy resolution so symlink aliases cannot select a weaker policy row.

Service operations target exact `.service` unit names and use the `systemctl --user` / `journalctl --user-unit` boundary.

The production topology uses a lingering systemd user manager for UID `atlas`. There is no runtime Polkit confirmation layer. Start/stop/restart authority comes only from runtime policy.

Self-restart is verification-sensitive. A successful dispatch enters `uncertain`; the successor process reconciles the occurrence using `INVOCATION_ID` rather than having the predecessor pretend it observed its own replacement.

## 11. Work and Cadence

Work persists an objective and ordered semantic capability steps. It does not persist a scalar authority allowance.

When a step runs it invokes `CapabilityRuntime`, so current policy governs the actual action. A pending confirmation pauses Work; success resumes the sequence; blocked/failed/expired actions fail the relevant step.

Cadence stores recurring duties and periodically materializes them as normal Work. It has no privileged execution path around Work or policy.

## 12. Models and providers

Provider settings are persistent runtime configuration. Credentials are stored separately in the encrypted credential store and are never returned through public provider state.

The provider runtime supports OpenAI-compatible chat endpoints, OpenAI Responses, Anthropic Messages and Gemini Generate Content adapters.

Enabled providers are attempted in runtime priority order. Transport/provider failure falls through to another enabled provider rather than changing Atlas semantics.

The model receives a bounded capability shortlist and can request a wider capability search. It returns either a conversational reply, a semantic capability selection or a capability-search request. Authorization is never delegated to the model.

## 13. Memory, Knowledge and Chat

Memory and Knowledge are separate runtime responsibilities. `KnowledgeStore` contains durable references and notes. `MemoryStore` contains owner-scoped persistent memory with its own `memory_items` table, FTS index, supersession chain and active/retracted state. Memory never enters `knowledge_items`, so `knowledge.search` cannot retain or return memory content.

Memory FTS uses the same `item_id UNINDEXED, title, content` column order as Knowledge. Recall orders by raw `bm25(memory_fts) ASC, updated_at DESC`; the raw score is intentionally not normalized because SQLite FTS5 bm25 is lower-is-better and scores are not comparable across queries.

Chat persists conversations and turns in `atlas-chat.db`. For each model decision it independently retrieves relevant active Memory and Knowledge, then combines those with bounded recent conversation and the live capability shortlist. The whole conversation history is not treated as runtime state.

After a conversational reply is already stored, Chat may run owner-turn memory reconciliation. That process produces a proposal only; it is not a capability and has no authority of its own. A proposed remember/update must be grounded in an exact substring of the authenticated owner turn, and update/retract targets must resolve to an existing owner memory candidate. Reconciliation then invokes the ordinary `memory.remember`, `memory.update` or `memory.retract` capability through `CapabilityRuntime`. `NO` therefore produces a blocked occurrence, `CONFIRM` produces an ordinary pending exact confirmation, and `YES` executes. There is no `memory.capture` operation and no policy pre-check in Chat. A capture outcome never rewrites or hijacks the reply already produced.

Memory policy is operation-specific. The visible initial policy is search `YES`, remember `YES`, update `YES`, retract `YES`, restore `CONFIRM`, purge `CONFIRM`. One operation never grants another.

### Governed memory purge

`MemoryStore`, `ActionStore` and evidence are physically co-located in `atlas-work.db` so purge has one real SQLite transaction boundary. `MemoryRuntime` refuses construction when the memory and action stores do not point to the same database. The runtime opens the transaction; each store owns SQL for its own table and accepts the caller-owned connection without committing or closing it. Any exception rolls the entire unit back before the executor returns failure.

The target supersession chain is resolved with a cycle-safe iterative breadth-first walk following `supersedes` in both directions. Rows are deleted explicitly rather than relying on foreign-key cascade behavior. FTS delete triggers execute inside the same transaction.

Purge matching is deliberately wider than item id alone but narrow to this principal's `atlas/memory` action history. For every memory in the chain Atlas builds hashes as `sha256(NFC -> collapse whitespace -> strip -> casefold)` over `content` and `grounding_excerpt`. A non-executing memory occurrence matches when it carries a chain item id anywhere in its stored JSON/summary or when a content-bearing `content`, `title`, `grounding_excerpt`, `text` or summary string has one of those normalized hashes. This reaches blocked/failed writes that never received an item id. Matching payload/result/receipt content and summary are replaced with `[purged]` and marked redacted; matching evidence content is scrubbed too. Action identity, state, timestamps, policy decision/revision and the original `payload_sha256` remain. A content-free `memory_purge_redaction` evidence row records occurrence id, match basis and redacted field names.

The purge occurrence itself persists only the target `item_id`; principal context comes from trusted invocation provenance at execution time and is not added to the attested payload. Because the purge occurrence is `executing` while the transaction runs, the terminal-status guard also prevents it from redacting itself.

Guaranteed. After memory.purge succeeds, within atlas-work.db, in one transaction or not at all: every memory_items row in the target supersession chain is deleted with its FTS entries; every action_occurrences row for this principal under atlas/memory in a terminal status that carries a chain item id or matching normalized content has its content-bearing fields and summary replaced; evidence rows for those occurrences are scrubbed the same way. Atlas will not surface that content again through recall, chat context assembly, capability results or the control plane.

Not guaranteed. The originating chat turns in atlas-chat.db (delete the conversation separately); text already sent to a model provider, subject to that provider's retention; payload_sha256, kept deliberately as attestation — a hash, not a copy; SQLite WAL frames, freelist pages and page slack (no VACUUM); any backup taken before the purge; any copy made outside Atlas.

Purge is application-level suppression plus content redaction. It is not forensic erasure of the storage medium.

Atlas deliberately does not run `VACUUM` or advertise `PRAGMA secure_delete` as part of this guarantee: neither can turn the cross-database/provider/backup boundary into atomic forensic erasure. Companion links source memories to the existing conversation-delete path so the owner can remove originating chat turns separately.

Tool outcomes are fed back into the conversational turn. Blocked and failed actions are grounded from the durable occurrence rather than rewritten as model speculation.

## 14. Persistence topology

The production instance root is external to the Git checkout. This prevents code deployment, rollback or repository replacement from implicitly replacing runtime state.

```text
atlas-identity.db
  principals, connections, service bindings, provider settings,
  MCP servers, source roots, append-only policy events

atlas-work.db
  Work, steps, governed action occurrences, evidence,
  Knowledge references/notes, first-class Memory + Memory FTS

atlas-chat.db
  conversations and turns

atlas-cadence.db
  recurring duties

secrets/
  AES-GCM credential store and master key
```

The v3 migration imports selected configuration and custody state only. Legacy authority grants, Work/Chat/Cadence data, HostAction state and embedded domain-product databases are not imported.

## 15. API and Companion

`atlas_api` provides authenticated read routes and CSRF-protected mutation routes. Session authentication establishes the owner principal; it is not an authority decision by itself.

Companion's primary navigation is Chat, Work, Sources and Atlas. Pending `CONFIRM` occurrences are also surfaced through the global `Needs you` affordance.

The Memory screen is a secondary durable-context surface reached from Sources; it exposes owner memory, grounding, retract/restore/purge controls and the exact purge guarantee. The Atlas screen exposes live policy, providers, MCP/n8n connections, external-account bindings, source roots, host state and capability inventory from the same runtime used for execution.

The Capabilities surface is generated from that live inventory rather than from provider-specific frontend modules. Server metadata creates provider groups, discovered tool names/metadata create presentation categories, and `input_schema` renders common string, number, boolean, enum, array and object inputs. Complex schemas retain a raw JSON fallback. Invocation still crosses `/api/capabilities/<id>/invoke`, so normalization, exact scope resolution, `NO` / `YES` / `CONFIRM`, evidence and verification remain runtime responsibilities. The UI treats non-exact native `scope_hint` values as hints rather than final authorization decisions; exact MCP tool scopes can expose direct policy controls.

The UI contains no parallel permission state.

## 16. Deployment

The production API is installed as the root-owned user unit:

```text
/etc/systemd/user/atlas-api.service
```

The `atlas` account has a real home, membership in `systemd-journal`, and lingering enabled. The user manager therefore survives logout and starts at boot.

The unit binds Atlas to `127.0.0.1:8080`; Caddy provides HTTPS and external reachability. `PrivateTmp` is deliberately absent because the target Ubuntu host restricts unprivileged user namespaces and the user-unit topology does not require that mount namespace.

The unit supplies startup mechanics only. It does not encode `NO`, `YES` or `CONFIRM` decisions.

## 17. Product boundary

Morning is absent from this runtime and repository architecture. It is served and operated as a separate product. Atlas does not own Morning state, routes, UI or domain logic.

If Morning is connected later, it must appear as an explicit external capability/API boundary and remains independently deployable.

## 18. Validation

The acceptance suite attacks the governing invariants: policy specificity/default deny, exact confirmation and policy recheck, Work recheck, memory operation-level authority, owner-grounded post-reply capture, atomic purge rollback, hash-based redaction without item ids, untouchable confirmation windows, cycle-safe supersession purge, raw lower-is-better bm25 recall, filesystem containment, Streamable HTTP/stdio MCP inventory and dispatch, provider discovery, user-systemd dispatch, API authentication/control and deployment-state assumptions.

Companion has independent lint, unit tests and a production/PWA build.

A green build is necessary but not sufficient for deployment. Live cutover also verifies the actual `atlas` user manager, persisted production state, HTTPS Companion, provider/MCP availability and self-restart reconciliation.
