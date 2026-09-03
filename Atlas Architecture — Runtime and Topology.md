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

1. identity/policy stores plus the explicit `WorkDatabase` boundary for `atlas-work.db`;
2. action/evidence stores and the canonical capability/action gate;
3. encrypted credentials and model/web provider settings;
4. local embedding-provider construction and MCP/n8n discovery;
5. enrolled local sources, host capabilities and external providers;
6. Knowledge, Memory V2, Work and Cadence;
7. Chat over the live registry, hybrid capability retriever and separate Knowledge/Memory recall stores.

`atlas_api` exposes the authenticated HTTP control plane and serves the built Companion PWA. It is not a second engine.

`WorkDatabase` is the sole supported connection shape for the shared `atlas-work.db` transactional domain. It enforces `sqlite3.Row`, foreign keys, a >=5 s busy timeout, `synchronous=FULL`, WAL mode and a loadable `sqlite-vec` extension; store classes participating in that domain receive this boundary rather than constructing independent SQLite connections.


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

Policy has two values: `NO`, `YES`.

Default seeded policy is coarse: whole top-level scope domains (`atlas/*`, `files/*`, `web/*`, each `host/*` domain) are seeded `YES` in one row rather than per fine-grained operation. An owner who wants a narrower boundary layers a more specific row on top, which wins by specificity. Host filesystem access to a fixed set of sensitive paths is the one exception: it is rejected by a hard code boundary (`HostRuntime.protected_paths`) before scope resolution runs at all, not by a policy row, so it never appears in the policy table.

Policy mutation is available only through authenticated mutation-control routes. Capabilities cannot call a policy-write capability because none exists.

## 5. Actions and execution

`ActionOccurrence` is the durable unit of governed execution. It records:

- capability id and semantic operation;
- normalized resource scope;
- normalized payload and SHA-256 digest;
- principal and surface provenance;
- policy decision, revision and matched event;
- action state;
- Work/step linkage where applicable;
- result, receipt and error fields;
- created, executed and completed timestamps.

Action state is `blocked | executing | succeeded | failed | uncertain`. There is no pending/confirmation state: `submit()` resolves policy exactly once against the normalized scope and payload — `NO` creates a terminal `blocked` occurrence, `YES` transitions straight to `executing` and runs the executor synchronously in the same call. There is no separate confirm/cancel step, no confirmation-window expiry, and no client-supplied payload beyond the original invocation, so there is nothing for the model or a client to bypass by supplying a confirmation flag.

The occurrence payload is immutable once created. The one deliberately separate terminal-history mutation is governed `memory.purge` redaction: it can only strip content from terminal memory occurrences and never introduces executable values or recomputes `payload_sha256`. If a *different* matching occurrence is still `executing`, purge fails closed and rolls back rather than leaving it unredacted — the purge occurrence's own `executing` row is exempt, since its payload can only ever hold the target `item_id`. `ActionStore.transition()` remains closed to payload updates.

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

Provider-specific functionality should enter Atlas through the generic capability boundary rather than by creating parallel domain runtimes. A provider may translate an external system's discovery/schema surface into MCP tools, but it does not define owner authority. Provider-owned response normalization is also allowed when an external API returns transport-heavy or encoded data: the provider may decode and compact that response into stable structured fields before it reaches generic Chat context, while retaining the same governed capability identity.

The implemented Google Workspace provider is the first example. It reads Google's Discovery Service for enabled Workspace APIs and exposes the resulting Gmail, Drive and Calendar methods as ordinary MCP tools over stdio. Current `gws` supplies Google authentication and API execution. Production uses the documented headless authorized-user credentials-file path rather than asking the `atlas` service account to mutate the owner's interactive gws config directory: the exported credential stays under protected external production state, and gws token/cache state lives in a config directory created and owned by UID `atlas`. The legacy interactive config remains owner-only. This technical credential state does not grant Atlas discretionary authority. Atlas does not maintain a separate semantic mail layer or a handcrafted list of allowed Gmail functions.

The governing path remains:

```text
Google Discovery / provider tool
        -> MCP capability
        -> exact mcp/<server>/tool/<tool> scope
        -> NO | YES
        -> provider execution
```

If Google exposes a method and the connected account is technically authorized for it, Atlas can inventory that method. Whether Atlas may invoke it is an owner-policy decision.

### Android phone provider

`atlas_providers.android_phone_mcp` is a deliberately narrow device provider, not a general remote shell. It can expose three Termux:API-backed tools when available: current location, telephony/cell inspection and SIM SMS. In server-side production mode the stdio MCP process reaches one enrolled Android device through SSH using a dedicated identity, strict known-hosts verification, batch mode and a fixed remote bridge command. The bridge accepts bounded JSON operations rather than arbitrary commands. MCP discovery still determines the live tool inventory, and each tool crosses the ordinary `mcp/<server>/tool/<tool>` policy scope before execution.


### Provider-neutral web capability layer

Current public-web access is expressed as stable Atlas capabilities rather than task-specific agents or provider-specific tools:

```text
Atlas reasoning / Work
        -> web.search | web.read | web.fetch | web.extract | web.download | web.crawl | web.browser.render
        -> exact web/search or web/<public-host> scope
        -> NO | YES
        -> replaceable search / HTTP provider
        -> structured evidence + provenance
```

The baseline read/fetch provider uses bounded direct HTTP. `web.search` becomes technically available only when an enabled, credentialed search provider is configured; current adapters support Brave Search, Jina Search, Tavily and Serper in priority/fallback order. Search credentials live in `CredentialStore`, never in the browser or process environment. Provider-native response streams terminate at a deterministic evidence-translation boundary before they enter Atlas reasoning: search adapters emit one stable search-result contract, while HTTP responses are parsed by media type into normalized page, structured-data, text-document or metadata-only evidence with an explicit translator version, `data_only` trust marker and provenance/content identity. Raw transport bytes and executable/presentation markup are not model context. Legitimate source wording is preserved as evidence rather than regex-sanitized simply because it resembles an instruction.

This evidence boundary is not an authority mechanism. Runtime policy remains the sole authority gate for actions: external data cannot grant `YES`, widen a scope or mutate policy merely because the source text requests it. Translation constrains the shape and provenance of external evidence; `CapabilityRuntime` / `ActionRuntime` enforce what Atlas may actually do.

Direct fetches accept only HTTP(S) on the normal web ports, reject credentials in URLs, resolve exclusively to public addresses, pin the connection to the validated address, and cap time and bytes. Every redirect target is canonicalized and independently resolved through the same public-address boundary. Cross-origin redirects are allowed for ordinary OEM/CDN flows, but an HTTPS request cannot downgrade to HTTP and credentials, cookies, API-key/token headers, origin and referrer are irreversibly stripped when the origin changes. Caller request headers and response headers remain separate so response metadata can never be replayed on a later hop; same-origin provider authorization survives a redirect. Crawl itself remains robots-aware, bounded to ten pages and same-origin so discovered links cannot silently widen one crawl action into a different policy scope. Downloads materialize without overwrite inside Atlas managed intake, gated by the same `web/*` policy scope as other web capabilities.

`web.browser.render` is an implemented read-only rendered-page capability backed by Playwright/Chromium. Static `web.read` remains the first path; deterministic structural quality signals can escalate an inadequate static page to `web.browser.render` without another model decision. The render action still crosses runtime policy independently. Browser network requests are forced through Atlas controlled HTTP transport, service workers/downloads are disabled, non-GET/HEAD requests are rejected, top-level navigation cannot silently widen to an unrelated host, and executable DOM state never crosses the evidence boundary: only normalized visible-text evidence, links and provenance reach reasoning. CAPTCHA/access-control detection is reported rather than bypassed. Form submission and general interaction are not exposed; adding them requires explicit mutation contracts and runtime-policy decisions rather than treating browser possession as authority.

## 9. Local filesystem

Source enrollment registers a canonical host directory with the hardened local-source kernel. Enrollment exposes technical read and mutation operations; it does not encode owner authority.

The kernel uses descriptor-relative operations, no-follow path handling, mount containment, regular-file checks, no-overwrite mutations and managed quarantine for deletion.

Policy scopes are rooted at:

```text
files/<provider-namespace>/<root-id>[/<canonical-relative-path>]
```

The destination of copy/move/rename/restore is normalized into the governed payload before hashing and policy resolution.

## 10. Host runtime

Host observation includes status, resources, storage and bounded filesystem inspection. Host filesystem paths are canonicalized before policy resolution so symlink aliases cannot select a weaker policy row.

Service operations target exact `.service` unit names and use the `systemctl --user` / `journalctl --user-unit` boundary.

The production topology uses a lingering systemd user manager for UID `atlas`. There is no runtime Polkit confirmation layer. Start/stop/restart authority comes only from runtime policy.

Self-restart is verification-sensitive. A successful dispatch enters `uncertain`; the successor process reconciles the occurrence using `INVOCATION_ID` rather than having the predecessor pretend it observed its own replacement.

Debian package management is split by privilege. `host.package.inspect` uses fixed read-only `dpkg-query`/`apt-cache` calls in the Atlas process. `host.package.install`, `host.package.remove` and `host.package.refresh` are available only when the root-owned Unix-socket package broker is present and trusted. The broker verifies the peer UID, accepts only exact Debian package names and fixed operations, and executes fixed `apt-get` vectors. Runtime `NO`/`YES` is resolved before the broker is reached; the broker is a technical privilege boundary, not a second authority system.

Host filesystem access to a fixed set of sensitive paths — `/etc/shadow`, `/etc/gshadow`, `/root`, the instance `secrets/` directory, `companion-auth.env`, and (when the instance root lives under `/home/<user>`) that user's `.ssh`/`.gnupg` — is rejected by `HostRuntime.protected_paths` before scope resolution runs, independent of policy content.


## 11. Work and Cadence

Work persists an objective and ordered semantic capability steps. It does not persist a scalar authority allowance.

When a step runs it invokes `CapabilityRuntime`, so current policy governs the actual action. An `uncertain` (unverified) outcome pauses Work in `waiting` until reconciled; success resumes the sequence; a `blocked` or `failed` action fails the relevant step.

Cadence stores recurring duties. `work_template` cadences materialize normal Work; `intake_sweep` cadences schedule bounded monitored-Source intake outside Work, where each Artifact is independently classified and may create one ordinary Work item. Neither kind has a privileged execution path around capability policy.

Chat is the authoring and interpretation surface for ordinary Work and `work_template` Cadence. It resolves real object ids, validates step inputs through capability schemas and invokes the same governed `work.create`, `cadence.create`, `cadence.update` and `cadence.run_now` capabilities used by other trusted surfaces. `intake_sweep` remains readable and runnable through these capabilities but is not conversationally creatable or editable in this iteration.

A Work/Cadence link into Chat may attach a cleaned `focused_reference` to one inference only. That reference is persisted on the originating owner turn as provenance; bounded later conversation can carry it as historical `reference_provenance`, but it is never promoted back into active focus or stored as sticky current-object state.

Successful presentable workflow mutations retain the latest action occurrence on the final assistant turn so Companion can render a WorkflowCard from durable runtime truth. This applies to `work.create`, `cadence.create`, `cadence.update` and owner-triggered `cadence.run_now`; transient tool context is not persisted wholesale. Scheduler-fired Cadence runs do not manufacture a Chat action card. Their results are read through the Cadence run reference/history and ordinary `work.get`/Work detail.

### Artifact intake before Work

A monitored Source event is not itself Work. Deterministic detection first establishes or resolves an Artifact. Atlas then performs one bounded semantic classification over that Artifact and selects a workflow intent. Work is created only when that classification establishes a real durable responsibility.

Classification describes purpose, Knowledge disposition and relationship; it does not prescribe file-modality mechanics such as OCR, table parsing, image extraction, chunking or embeddings. Runtime-owned workflow templates translate an approved workflow intent into capability steps. Model output can therefore select `knowledge.ingest` but cannot manufacture its executable steps.

Routed Work keeps its opaque `work_id` as immutable identity and may also carry a human display reference such as `AA-001`: first letter = Artifact/responsibility class, second letter = workflow class, number = per-route sequence. Display references are descriptive history, never identity or authority.

Work inputs may use backward-only `$ref` references to completed prior-step outputs. References are resolved before capability invocation and policy resolution and fail closed on invalid or forward pointers.

## 12. Models and providers

Provider settings are persistent runtime configuration. Credentials are stored separately in the encrypted credential store and are never returned through public provider state.

The provider runtime supports OpenAI-compatible chat endpoints, OpenAI Responses, Anthropic Messages and Gemini Generate Content adapters.

Enabled providers are attempted in runtime priority order. Transport/provider failure falls through to another enabled provider rather than changing Atlas semantics, and the failed provider is logged as an operational warning. Decrypted model credentials are passed directly from `CredentialStore` to the selected in-process adapter; they are never round-tripped through process-global environment variables.

The model receives a bounded capability shortlist and can request a wider capability search. `CapabilityRetriever` builds that shortlist from live registry documents using exact/tool-name matches, weighted deterministic sparse matching and dense local embeddings, then fuses sparse/dense rank positions with reciprocal-rank fusion. The index fingerprint is derived from live definitions/schemas/selected metadata, so registry changes rebuild the in-memory retrieval representation. The default embedding provider is FastEmbed `BAAI/bge-small-en-v1.5`; its model revision, package version, dimensions, normalization and representation version are explicit `EmbeddingSpec` identity. A deterministic hash embedder exists only for tests/offline diagnostics. None of this changes capability truth or authority.

The model returns either a conversational reply, a semantic capability selection or a capability-search request. Authorization is never delegated to the model. Capability-search fallback is limited to the small core signpost set rather than an alphabetical registry slice. Tool-result bounding preserves intact capability/status/trust envelopes, caps oversized item content, and omits older results first when the turn budget is exceeded.

### PDF-derived Knowledge

The first binary Knowledge extraction contract is `pdf@1`. It reads complete source bytes under a separate 64 MiB extraction cap, emits deterministic page text and detected tables, and records `extractor:pdf@1` in derived Artifact provenance. OCR, layout semantics and image/diagram interpretation are outside this extractor; unresolved visual modalities remain explicit inspection state.

Heavy semantic representations sit behind a replaceable provider boundary. Intake classification may declare semantic representation needs such as OCR without naming an implementation. Runtime maps an executable need to `representations.derive`, which is governed against the concrete Source scope, hands complete bounded bytes to an external subprocess, materializes provider output in the managed derived area, and registers a lineage-bearing derived Artifact. Provider subprocesses receive a minimal environment rather than Atlas/model credentials. Unsupported or unavailable representation needs fail closed before Work is created.

The first live representation provider is OCR-only: `atlas_providers/representation_ocr.py` runs RapidOCR/ONNX Runtime in a separate external provider environment, using PyMuPDF only to rasterize PDF pages. Provider capability advertisement is need-specific, so configuring OCR never implies that layout, table semantics or visual interpretation are available.

## 13. Memory, Knowledge and Chat

Memory and Knowledge are separate runtime responsibilities. `KnowledgeStore` contains durable references and notes. Current `MemoryStore` is Memory V2: canonical owner data lives in `memory_v2_items` with integer internal keys, stable public item ids, explicit supersession foreign keys and active/superseded/retracted state. Memory never enters Knowledge tables.

Memory retrieval has two derived channels. `memory_v2_fts` provides lexical candidates; a versioned `sqlite-vec` table provides dense cosine candidates for the active embedding generation. Atlas fuses their rank positions with reciprocal-rank fusion (sparse weight 1.1, dense 1.0) rather than pretending FTS bm25 and cosine distance are directly comparable. Vector generations record provider, model, model revision, package version, dimensions, normalization and representation version. When that identity changes, Atlas builds and verifies a candidate generation, activates it, and retires the prior generation. Canonical memory rows remain the source of truth.

The previous `memory_items`/`memory_fts` schema is not part of live Memory V2 queries. `LegacyMemoryStore` is retained only as an explicit adapter for historical data; the runtime does not silently migrate those rows on startup. This is intentionally documented so preserved legacy rows are not mistaken for currently recalled Memory.

Knowledge passage retrieval remains FTS5-based today. Dense/vector retrieval in this implementation therefore applies to Memory V2 and capability discovery, not yet to the OEM/Knowledge passage store.

Chat persists conversations and turns in `atlas-chat.db`. For each model decision it independently retrieves relevant active Memory and Knowledge, then combines those with bounded recent conversation and the hybrid live capability shortlist. The whole conversation history is not treated as runtime state.

After a conversational reply is already stored, Chat may run owner-turn memory reconciliation. That process produces a proposal only; it is not a capability and has no authority of its own. A proposed remember/update must be grounded in an exact substring of the authenticated owner turn, and update/retract targets must resolve to an existing owner memory candidate. Reconciliation then invokes the ordinary `memory.remember`, `memory.update` or `memory.retract` capability through `CapabilityRuntime`. `NO` produces a blocked occurrence and `YES` executes. There is no `memory.capture` operation and no policy pre-check in Chat. A capture outcome never rewrites or hijacks the reply already produced.

Memory policy is resolved per operation, the same as any other capability: `search`, `remember`, `update`, `retract`, `restore` and `purge` are independently addressable scopes, and one operation never grants another. The seeded default does not distinguish between them — all of `atlas/memory/*` inherits the coarse `atlas/*` → `YES` domain seed — so an owner who wants `restore`/`purge` to require a narrower authorization adds an explicit, more-specific policy row for that operation.

### Governed memory purge

`MemoryStore`, `ActionStore` and evidence are physically co-located in `atlas-work.db` so purge has one real SQLite transaction boundary. `MemoryRuntime` refuses construction when the memory and action stores do not point to the same database. The runtime opens the transaction; each store owns SQL for its own table and accepts the caller-owned connection without committing or closing it. Any exception rolls the entire unit back before the executor returns failure.

The target supersession chain is resolved with a cycle-safe iterative breadth-first walk following `supersedes` in both directions. Rows are deleted explicitly rather than relying on foreign-key cascade behavior. FTS delete triggers execute inside the same transaction.

Purge matching scans every occurrence in this principal's `atlas/memory` action history regardless of status, and is deliberately wider than item id alone. For every memory in the chain Atlas builds hashes as `sha256(NFC -> collapse whitespace -> strip -> casefold)` over `content` and `grounding_excerpt`. A memory occurrence matches when it carries a chain item id anywhere in its stored JSON/summary or when a content-bearing `content`, `title`, `grounding_excerpt`, `text` or summary string has one of those normalized hashes. This reaches blocked/failed writes that never received an item id. A matching occurrence in a terminal status has its payload/result/receipt content and summary replaced with `[purged]` and marked redacted; matching evidence content is scrubbed too. Action identity, state, timestamps, policy decision/revision and the original `payload_sha256` remain. A content-free `memory_purge_redaction` evidence row records occurrence id, match basis and redacted field names.

A matching occurrence that is still `executing` — other than the purge occurrence itself — aborts the whole purge instead of being skipped: its content isn't visible to redact yet, and it could land moments later still carrying the text purge was meant to remove, so the transaction rolls back rather than complete around it. The purge occurrence's own `executing` row is the one exemption (matched by capability id, not status), because its payload can only ever hold the target `item_id`, never memory content.

Guaranteed. After `memory.purge` succeeds, within `atlas-work.db`, in one transaction or not at all: every `memory_v2_items` row in the target supersession chain is deleted together with its FTS entries, vector rows and generation-representation links; every `action_occurrences` row for this principal under `atlas/memory` in a terminal status that carries a chain item id or matching normalized content has its content-bearing fields and summary replaced; evidence rows for those occurrences are scrubbed the same way. Purge also cannot succeed while a different matching write is still in flight, so the guarantee holds even against that race rather than being invalidated by it. Atlas will not surface that content again through live Memory V2 recall, chat context assembly, capability results or the control plane.

Not guaranteed. The originating chat turns in atlas-chat.db (delete the conversation separately); text already sent to a model provider, subject to that provider's retention; payload_sha256, kept deliberately as attestation — a hash, not a copy; SQLite WAL frames, freelist pages and page slack (no VACUUM); any backup taken before the purge; any copy made outside Atlas.

Purge is application-level suppression plus content redaction. It is not forensic erasure of the storage medium.

Atlas deliberately does not run `VACUUM` or advertise `PRAGMA secure_delete` as part of this guarantee: neither can turn the cross-database/provider/backup boundary into atomic forensic erasure. Companion links source memories to the existing conversation-delete path so the owner can remove originating chat turns separately.

Tool outcomes are fed back into the conversational turn. Blocked and failed actions are grounded from the durable occurrence rather than rewritten as model speculation.

## 14. Persistence topology

The production instance root is external to the Git checkout. This prevents code deployment, rollback or repository replacement from implicitly replacing runtime state.

```text
atlas-identity.db
  principals, connections, service bindings, model/web provider settings,
  MCP servers, source roots, append-only policy events

atlas-work.db
  Work, steps, governed action occurrences, evidence, Artifacts/Library,
  Knowledge references/passages, Memory V2 canonical rows + FTS + sqlite-vec generations

atlas-chat.db
  conversations and turns

atlas-cadence.db
  recurring duties

secrets/
  AES-GCM credential store and master key
```

The v3 migration imports selected configuration and custody state only. Legacy authority grants, Work/Chat/Cadence data, HostAction state and embedded domain-product databases are not imported.

## 15. API and Companion

`atlas_api` provides authenticated read routes and CSRF-protected mutation routes. Session authentication establishes the owner principal; it is not an authority decision by itself. Long-running synchronous runtime work — model turns, generic capability execution, Work execution, MCP refresh and provider verification — is dispatched through Starlette's worker threadpool rather than executed on the asyncio event loop, so health and polling remain responsive during a slow turn.

Companion opens on `/chat` as one owner surface rather than a permanent four-item top navigation. The surface interweaves the conversation with Active Work, Cadence, `Needs you`, runtime readiness and Control awareness; a right-hand operational margin exposes active Work, scheduled duties and deeper plumbing links. Work, Cadence, Sources, Memory, Operations and Atlas Control remain routed deep views. There is no pending-confirmation state to surface; owner attention is driven by discovery errors, missing provider credentials and unavailable capabilities instead.

The Memory screen is a secondary durable-context surface reached from Sources; it exposes owner memory, grounding, retract/restore/purge controls and the exact purge guarantee. The Atlas screen exposes live policy, providers, MCP/n8n connections, external-account bindings, source roots, host state and capability inventory from the same runtime used for execution. A capability snapshot reads the owner's latest policy rules and revision once, then resolves the inventory against that in-memory snapshot instead of reopening SQLite and rescanning policy for every capability.

The Capabilities surface is generated from that live inventory rather than from provider-specific frontend modules. Server metadata creates provider groups, discovered tool names/metadata create presentation categories, and `input_schema` renders common string, number, boolean, enum, array and object inputs. Complex schemas retain a raw JSON fallback. Invocation still crosses `/api/capabilities/<id>/invoke`, so normalization, exact scope resolution, `NO` / `YES`, evidence and verification remain runtime responsibilities. The UI treats non-exact native `scope_hint` values as hints rather than final authorization decisions; exact MCP tool scopes can expose direct policy controls.

The UI contains no parallel permission state.

## 16. Deployment

The production API is installed as the root-owned user unit:

```text
/etc/systemd/user/atlas-api.service
```

The `atlas` account has a real home, membership in `systemd-journal`, and lingering enabled. The user manager therefore survives logout and starts at boot.

The unit binds Atlas to `127.0.0.1:8080`; Caddy provides HTTPS and external reachability. `PrivateTmp` is deliberately absent because the target Ubuntu host restricts unprivileged user namespaces and the user-unit topology does not require that mount namespace.

The unit supplies startup mechanics only. It does not encode `NO` or `YES` decisions. Companion authentication requires a stable configured session secret; Atlas does not silently generate a process-local replacement on boot. Caddy is the intended loopback proxy, and login-throttle handling trusts `X-Forwarded-For` only when the direct peer is loopback.

## 17. Product boundary

Morning is absent from this runtime and repository architecture. It is served and operated as a separate product. Atlas does not own Morning state, routes, UI or domain logic.

If Morning is connected later, it must appear as an explicit external capability/API boundary and remains independently deployable.

## 18. Validation

The acceptance suite attacks the governing invariants: policy specificity/default deny, Work recheck, memory operation-level authority, owner-grounded post-reply capture, atomic purge rollback, hash-based redaction without item ids, untouchable executing-occurrence windows, cycle-safe supersession purge, raw lower-is-better bm25 recall, filesystem containment, Streamable HTTP/stdio MCP inventory and dispatch, provider discovery, user-systemd dispatch, API authentication/control and deployment-state assumptions.

Companion has independent lint, unit tests and a production/PWA build. Runtime hardening tests additionally pin event-loop responsiveness during a slow chat turn, direct (non-environment) model credential custody, one policy-store snapshot per capability inventory, intact bounded tool-result envelopes and core-signpost-only fallback discovery.

A green build is necessary but not sufficient for deployment. Live cutover also verifies the actual `atlas` user manager, persisted production state, HTTPS Companion, provider/MCP availability and self-restart reconciliation.
