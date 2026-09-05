# Atlas

Atlas is a single persistent operational agent for durable personal work.

The agent is the runtime. Models, providers, MCP servers, n8n workflows, filesystem access and host operations are capabilities that Atlas can use; they are not separate agent identities.

The current implementation is a clean runtime built around one governing boundary:

```text
intent
  -> capability
  -> deterministic scope resolution
  -> owner runtime policy
  -> NO | YES
  -> execution invariants
  -> provider / OS
  -> evidence + verification
```

## Product boundaries

Atlas is a standalone product. Morning is a separate standalone product and is not embedded in this repository, runtime, database or UI. Any future relationship between the products must use an explicit external API or MCP boundary.

Companion is Atlas's owner UI. It exposes runtime truth and control; it does not create a second authority model.

## Current implementation

Atlas currently includes:

- authenticated conversational Chat;
- durable Work and recurring Cadence, with governed compound-Artifact inspection and semantic intake/classification occurring before Work is created;
- an append-only owner policy with literal `NO` and `YES` decisions;
- full discovered MCP and n8n capability inventory;
- generic MCP over Streamable HTTP and local stdio transports;
- a Discovery-driven Google Workspace provider surface for Gmail, Drive and Calendar;
- runtime-managed model providers and encrypted credentials, with decrypted model keys passed directly to provider adapters rather than process-global environment variables;
- hardened enrolled filesystem roots, including governed in-browser file viewing;
- deterministic library consolidation that hashes whole enrolled roots, preserves originals, and materializes one canonical copy of each exact-content file into an Atlas-owned clean library;
- host observation and user-systemd service operations;
- durable Knowledge for references/notes and first-class persistent Memory with supersession, retraction and governed purge;
- Memory V2 hybrid retrieval over canonical rows using SQLite FTS5 + `sqlite-vec`, local FastEmbed embeddings, versioned index generations and reciprocal-rank fusion;
- hybrid capability discovery over the live registry using deterministic sparse matching + local semantic embeddings, without making the embedding model an authority source;
- a narrow Android-phone MCP provider that reaches enrolled Termux:API services over locked-down SSH for location, telephony inspection and SIM SMS;
- host Debian package inspection plus governed install/remove/metadata-refresh through a separately installed root-owned Unix-socket broker;
- action receipts, evidence and restart reconciliation;
- a responsive Starlette control plane that dispatches blocking model/tool/Work execution off the event loop;
- operational warning logs for non-fatal cadence, provider-fallback and post-turn reconciliation failures;
- the Companion PWA and runtime-control surface.

## Authority

Capability is not authority. Provider configuration, systemd, MCP credentials and source enrollment make operations technically available; they do not grant Atlas permission to perform them.

Owner discretion lives only in the running policy store. A policy change takes effect on the next resolution without a service restart, redeploy or provider reconfiguration.

The semantics are exact:

- `NO`: do not execute;
- `YES`: execute;
- no matching policy row: `NO`.

Policy is resolved after the resource scope and payload are normalized, and execution follows in the same request — there is no separate confirmation step to re-resolve later. Work steps cross the same gate when they actually run.

Policy history is append-only. More-specific resource scopes override broader scopes. Default seeding is coarse — whole top-level domains (`atlas/*`, `files/*`, `web/*`, each `host/*` domain) are seeded `YES` in one row — and an owner narrows a specific operation or resource with a more specific row rather than a separate mechanism. The one exception is host access to a fixed set of sensitive filesystem paths, which is a hard code boundary ahead of policy, not a policy row.

Authentication, provider attestation, schema validation, filesystem containment and other technical constraints remain execution invariants rather than competing permission systems.

## Capability inventory

`CapabilityRegistry` is the complete runtime inventory. Registration and discovery never imply permission.

Native capabilities include Artifacts/intake, Knowledge, Memory, Work, Cadence, local sources and host operations. Every tool advertised by an enabled MCP server is registered dynamically. n8n is treated as an MCP-backed capability provider and is not restricted to a handcrafted subset. MCP transport may be Streamable HTTP or a locally spawned stdio provider.

Each capability definition carries its discovered input schema. Companion consumes that same live inventory to group tools by provider/category, render common schema fields, expose availability and authority, and invoke the generic capability endpoint. Unsupported or unusually complex schemas fall back to editable JSON rather than requiring a provider-specific screen.

Production n8n is connected through n8n's built-in instance MCP endpoint as `n8n-runtime`. Atlas inventories the complete tool surface advertised by that server and applies the same runtime policy to each exact tool scope. The MCP bearer credential is technical provider custody in Atlas's encrypted credential store; it is not authority. The obsolete mail-only n8n connector is not registered in Atlas.

Google Workspace is integrated as a provider rather than a mail subsystem: Google Discovery defines the Gmail, Drive and Calendar method surface, the provider exposes those methods through MCP, and Atlas policy governs each discovered tool. Provider adapters may normalize excessively verbose external response formats into compact structured results before they cross the generic MCP boundary; Chat does not contain Gmail-specific parsing or truncation logic. Atlas does not maintain a parallel `mail.read` / `mail.send` semantic API. The provider keeps its `gws` OAuth/config state under the external production-state tree rather than inside the Git checkout.

The conversational model selects capabilities and arguments. It does not decide whether the operation is allowed. Chat's schema-rich shortlist is retrieved from the live registry with a hybrid ranker: exact/tool-name matches and deterministic sparse metadata/schema matching are fused with local dense embeddings by reciprocal-rank fusion. The current production embedding implementation is FastEmbed `BAAI/bge-small-en-v1.5`; its model snapshot/package/dimension/normalization identity is explicit and it remains derived retrieval machinery, not durable capability truth. Tool-result bounding preserves capability/status/trust envelopes and drops older results before corrupting the newest result into an unusable raw JSON tail. An unmatched capability query falls back only to the small core signpost set, never to an alphabetical slice of the registry.

Chat may execute ordinary in-turn reads, retrieval chains and bounded actions directly, while Work owns responsibilities that must survive the turn. That boundary has a runtime floor: after scope resolution a capability can require durable Work, and `CapabilityRuntime` refuses the ephemeral invocation before dispatch. The self-restart path derives Atlas's user-systemd identity from configured state and a cgroup/MainPID check, fails on disagreement, and refuses user-service restart when identity is unknown. Exact unresolved/refused actions are non-replayable within one Chat turn; Chat may observe an uncertain occurrence or adopt that same occurrence into Work when a residual obligation remains. Work step/action binding is persisted before executor dispatch, restart evidence survives recovery, restart throttling is timestamp-based rather than row-bounded, and terminal Chat-origin Work posts through one deduplicated completion path in both ordinary execution and restart recovery.

The API and Companion expose deployment truth directly: the running API reports the Git revision it loaded, the frontend is stamped with its build revision, and a mismatch is surfaced in the owner UI. `chat.turn` planner calls retain compact provider/latency/finish/usage/parse diagnostics; one unusable result is retried once and a second failure is reported as `planner_unavailable` without executing a guessed route.

## Persistence

Production state is separate from the code checkout. The deployed instance root is:

```text
/home/jaco/Projects/atlas-agent-state/production
```

The fresh stores are:

```text
atlas-identity.db   identity, provider settings, MCP servers, source roots, policy
atlas-work.db       Work-domain stores, action/evidence, Knowledge, Memory V2, sqlite-vec derived indexes
atlas-chat.db       conversations and chat turns
atlas-cadence.db    recurring cadence definitions
secrets/            encrypted credential database and master key
library-clean/      Atlas-owned non-destructive canonical-copy output for library consolidation
```

Old Atlas Work, Chat, Cadence, HostAction, authority-grant and Morning databases are not reused by this runtime.

### Google Workspace provider state

The production Google Workspace provider is a stdio MCP provider, not an Atlas mail subsystem. Its external runtime files live under the production state root:

```text
bin/gws                                      current Google Workspace CLI binary
google-workspace/headless-authorized-user.json headless authorized-user credential exported from gws
google-workspace/runtime-config/                Atlas-owned gws token/cache state
google-workspace/config/                        owner-only legacy interactive OAuth state
google-workspace/workspace/                     bounded provider upload/download workspace
```

The registered `google-workspace` MCP server launches `atlas_providers.google_workspace_mcp`, which derives Gmail, Drive and Calendar tools from Google Discovery and dispatches them through the current `gws` binary. Production uses gws's documented headless credentials-file flow: the exported authorized-user JSON remains outside Git and is readable only by the owner and the `atlas` service account, while gws writes refresh/cache state into an Atlas-owned `runtime-config/` directory. The older interactive config remains owner-only and is not used by the running service. This OAuth/config state is technical provider custody; Atlas `NO` / `YES` policy remains the sole discretionary authority.

The stdio MCP boundary keeps one provider session alive across calls and reconnects only after transport failure; it does not replay a failed invocation. Discovery documents are cached on disk with stale-cache fallback, and inventory refresh is discover-then-swap so a transient refresh failure retains the last known-good capability set while surfacing stale status. Provider errors preserve their concrete message for bounded Chat recovery, advertised schemas are size/depth bounded, and normalized capability-id collisions are rejected rather than shadowed. Google Workspace response normalization is exact-binding-specific: Gmail message/thread reads are compacted and MIME text is decoded before model context, while Drive metadata reads remain metadata operations unless media download is explicitly requested.

## Persistent Memory

Memory is a first-class runtime responsibility, not a `knowledge_items` subtype. The current `MemoryStore` is Memory V2: canonical owner rows live in `memory_v2_items`, lexical search lives in FTS5, and semantic search lives in versioned `sqlite-vec` index generations. Search fuses sparse and dense rankings by reciprocal-rank fusion. The canonical rows remain truth; embedding vectors and FTS state are derived/searchable representations that can be rebuilt when the embedding identity changes. `KnowledgeStore` remains separate, and Chat recalls Memory and Knowledge independently.

The previous `memory_items` table is not queried by Memory V2 and is not migrated automatically. `atlas_core.memory.legacy.LegacyMemoryStore` exists as an explicit legacy adapter; production data in the old table remains preserved until an intentional migration is performed. Knowledge passage retrieval is still FTS-based today; the new dense index is not a claim that OEM/Knowledge retrieval has already been converted to vectors.

Owner-turn auto-capture runs only after the conversational reply is already produced. It reconciles an exact owner-grounded excerpt into the existing governed `memory.remember`, `memory.update` or `memory.retract` capabilities. There is no `memory.capture` authority shortcut: each real operation resolves its own live `NO` / `YES` policy.

`memory.purge` is application-level suppression plus content redaction. It deletes the whole supersession chain and its FTS rows and scrubs matching terminal `atlas/memory` action/evidence content in one `atlas-work.db` transaction. It deliberately retains action identity, state, policy history and the original `payload_sha256` attestation. The originating conversation is stored separately in `atlas-chat.db` and must be deleted separately if the owner wants those source turns removed. Atlas does not claim forensic storage erasure.

## Companion

Companion now opens on one owner surface rather than a permanent four-item product nav. The `/chat` surface combines the current conversation with an operational-awareness strip for Active Work, Cadence, Needs you and Control, plus a live right margin for active Work, scheduled duties and deeper plumbing links. Work, Cadence, Sources, Memory and Control remain real routes and deep operational views, but they are subordinate to the owner surface rather than competing top-level products.
Chat transport recovery follows durable state rather than HTTP status: after a recoverable disconnect, Companion refetches the owner turn and any Chat-origin Work, clears the transient request error once durable ownership is found, and continues following the conversation until the Work completion turn arrives.

`/atlas` is the technical Control surface. It shows and edits the live runtime configuration: policy, providers, MCP/n8n servers, external account bindings, source roots, host state and capability inventory. The Capabilities view is runtime-driven: newly discovered tools appear without a frontend code change, and their advertised schemas drive the generic input form and execution controls.

There is no `Now` page and no Morning UI. The PWA manifest, favicon and installed-app icons use the current blue/cyan/violet Atlas identity; legacy gold startup artwork is not part of the checked-in interface.

Secrets are never returned to the browser. Atlas-managed model/MCP bearer credentials live in the encrypted `CredentialStore`; decrypted model API keys are handed directly to the in-process provider adapter and are not copied into `os.environ`, so Atlas-spawned subprocesses do not inherit them. Provider-owned external credentials such as the Google Workspace headless authorized-user file remain under the protected production-state tree and never enter Git or Companion state.

## Deployment topology

The production service is a root-owned user-systemd unit at:

```text
/etc/systemd/user/atlas-api.service
```

The `atlas` account has lingering enabled, so its user manager starts at boot. The unit runs inherently as UID `atlas`; it does not use `User=` / `Group=` directives and does not rely on Polkit for self-service management.

Atlas binds only to loopback. Caddy provides the external HTTPS boundary:

```text
Internet -> Caddy -> 127.0.0.1:8080 -> atlas-api.service
```

Host service capabilities use `systemctl --user`. Runtime policy, not systemd, decides whether status, logs, start, stop or restart are `NO` or `YES`. Read-only system-service inspection uses the system manager separately. Debian package mutations never run through arbitrary shell or sudo from Atlas: when installed, the root-owned `atlas-package-broker` accepts only the Atlas service UID over `/run/atlas-package-broker/control.sock` and dispatches fixed `apt-get` vectors after the ordinary runtime gate.

The optional `android-phone` stdio MCP provider is also an external boundary. Production currently reaches the owner's Termux device through a pinned SSH identity/known-hosts bridge and exposes only the provider-advertised location, telephony-inspection and SMS tools; each discovered tool remains governed as an ordinary MCP capability.

## Development

Requirements: Python 3.12, `uv`, Node.js 22+ and npm.

```bash
git clone https://github.com/Keeladin/atlas-agent.git
cd atlas-agent
uv sync --frozen
cd companion && npm ci && npm run build && cd ..
uv run pytest -q
```

Run a local instance:

```bash
uv run python -m atlas_api \
  --host 127.0.0.1 \
  --port 8080 \
  --instance-root ./instance \
  --static-dir ./companion/dist
```

`instance/companion-auth.env` must contain both the Companion password and a stable session secret before login can be used. Atlas fails startup rather than generating an ephemeral session secret that would invalidate every login on restart. Behind the production loopback proxy, `X-Forwarded-For` is trusted for login throttling only when the direct peer is a loopback address.

## Repository layout

```text
atlas_core/
  actions/        canonical action execution gate
  capabilities/   inventory, schemas and scope resolution
  policy/         append-only NO/YES policy
  identity/       principals, account connections and technical bindings
  providers/      model contracts, HTTP adapters and runtime settings
  mcp/            generic MCP/n8n discovery and dispatch
  mcp_stdio.py     generic local stdio MCP transport
  sources/        hardened local filesystem kernel
  work/           durable work and steps
  cadence/        recurring duties
  chat/           conversational orchestration
  host.py         host observation and user-systemd capabilities
  database/       explicit atlas-work.db connection/invariant boundary (WAL, FK, sqlite-vec)
  retrieval/      embedding contracts, hybrid capability retrieval and rank fusion
  memory/         Memory V2 canonical rows, hybrid recall, supersession and atomic purge
  knowledge.py    durable references and notes
  secrets.py      encrypted host-local credential store
atlas_api/        authenticated Starlette composition/control plane
atlas_providers/  external capability-provider adapters (Google Workspace, Android phone, representations)
companion/        React PWA owner interface
deploy/           user-systemd and reverse-proxy deployment definitions
tests/            architecture and acceptance invariants
```

## Verification

The canonical local checks are:

```bash
uv run pytest -q
uv run python -m compileall -q atlas_api atlas_core atlas_providers tests
cd companion
npm run lint
npm run test
npm run build
```

CI runs the same categories of checks on every push and pull request to `main`.

## Documentation

- `Atlas Constitution.md` — governing product principles.
- `Atlas Product Definition.md` — responsibilities and product boundary.
- `Atlas Architecture — Runtime and Topology.md` — implemented runtime architecture.
- `Atlas Interface Design.md` — current Companion visual and interaction doctrine.
- `P2` through `P5` documents — historical implementation-slice records; each now carries a current-status note where later slices supersede an earlier assumption.

When documentation and implementation disagree, inspect the implementation and tests and correct the documentation. Runtime truth wins over stale architectural prose.
