# CLAUDE.md — Atlas v3 engineering invariants

Persistent working instructions for Claude sessions in this repository. Keep it current; when it disagrees with the code, the code and tests win — fix the drift.

Atlas v3 is a clean greenfield runtime (`version 3.0.0`). Do not carry assumptions from the archived pre-v3 implementation. In particular, the old rule that "an explicit owner command grants natural-scope authority" is **deleted**. In v3 discretionary authority comes only from the runtime owner policy described below.

## What Atlas is

One persistent operational agent. Models, providers, MCP servers, n8n workflows, filesystem roots and host operations are **capabilities**, not separate agents and not durable identities. The composition root is `atlas_api.compose.build_runtime(instance_root)`; `atlas_api` is the authenticated HTTP control plane and PWA host, not a second engine.

The canonical chain, and every consequential action crosses it:

```
intent (Chat / Work / Cadence / control)
  → capability (schema-validated)
  → deterministic scope + payload normalization
  → OwnerPolicy.resolve → NO | YES | CONFIRM
  → execution invariants (auth, containment, attestation, schema)
  → provider / OS
  → evidence + verification
```

## Load-bearing invariants

**Capability ≠ authority.** Registering a capability, discovering an MCP tool, enrolling a source root, attesting an account or having OS access makes something *technically possible*. None of it grants permission. `CapabilityRegistry` is inventory only.

**Owner policy is the sole discretionary authority**, and it is literal: `NO` (do not execute), `YES` (execute), `CONFIRM` (durable exact-action confirmation, then execute). **No matching policy row resolves to `NO`** (`OwnerPolicy.resolve`, `atlas_core/policy/`). Policy events are append-only with SQLite update/delete triggers; resolution takes the most specific matching scope (more path segments win; exact operation beats `*`; latest sequence breaks ties). Sensitive paths are ordinary visible `NO` rows, never a hidden deny layer.

**Policy is not model-callable and not client-spoofable.** No policy-write capability exists; policy mutates only through the authenticated `POST /api/policy` control route. The model selects capabilities and arguments and *never* authorizes; a client cannot smuggle a `confirmed=true` flag into execution.

**Confirmation is an execution state, not a second permission system** (`ActionRuntime`, `atlas_core/actions/`). A `CONFIRM` creates a durable `pending_confirmation` `ActionOccurrence` binding capability, normalized operation, normalized resource scope, canonical payload SHA-256 and principal. Confirmation is accepted only from the same authenticated principal, expires after `CONFIRM_MAX_AGE` (5 min), and **policy is re-resolved immediately before execution** — a current `NO` blocks with `policy_revoked_before_execution`. There is no payload mutation in the confirm→execute window; the only mutation path is governed memory-purge redaction of terminal occurrences, which strips content, never recomputes `payload_sha256`, and cannot run against `pending_confirmation` or `executing`. `confirm` takes no client payload. State transitions are compare-and-swap; a replayed confirm hits a non-pending status and fails.

**Deterministic work stays deterministic.** Parsing, scope/payload normalization, hashing, policy resolution, persistence, filesystem containment and verification are code, not model calls. Do not ask a model to decide what code can decide exactly.

**Evidence and verification precede completion.** Consequential actions produce durable occurrences, receipts and evidence rows. A model asserting success is not evidence. Verifiable outcomes stay `uncertain` until reconciled — self-restart enters `uncertain` and the successor process reconciles via `INVOCATION_ID` (`HostRuntime.reconcile_self_restart`), never the predecessor claiming to have observed its own replacement.

**Work must not freeze authority.** `WorkItem` stores an objective and ordered capability steps, never a scalar authority allowance. Each step crosses `CapabilityRuntime` → policy **when it actually runs**, so a policy change after Work was created still governs the eventual side effect. `CONFIRM` pauses Work (`waiting_confirmation`); a later `NO` fails the step. Cadence materializes recurring duties as ordinary Work with no privileged path around policy.

**MCP/n8n discovery populates inventory, not permission.** Every advertised tool of an enabled server is registered under scope `mcp/<server-id>/tool/<tool-name>` (operation `invoke`). `n8n` kind changes metadata, not authority. Atlas supports Streamable HTTP and locally spawned stdio MCP transports. The technical transport reaches a tool only after the policy gate; `MCPRuntime.call_tool` is post-gate transport and must never be called to skip it. Production n8n uses the built-in instance MCP server as `n8n-runtime`; do not reintroduce mail-only or handcrafted workflow wrappers.

**Do not rebuild a semantic mail layer.** Google Workspace is an external capability provider: Google Discovery defines the Gmail, Drive and Calendar API method surface, the provider exposes it as MCP tools, and Atlas policy governs those exact tools. Provider-owned response normalization may decode/compact transport-heavy API results before they enter generic Chat context; do not move Gmail MIME/header semantics into ChatRuntime. Do not add parallel `mail.read`, `mail.send`, `mail.modify` aliases or a handcrafted Gmail whitelist. Provider ergonomics may improve presentation, but must not create a second executable authority path for the same external action. Keep provider OAuth/config state outside the Git checkout; technical provider credentials remain capability/custody, never owner policy.

**Android phone access is a narrow provider, never a remote-shell shortcut.** `atlas_providers.android_phone_mcp` may expose location, telephony inspection and SMS through Termux:API; server-side mode reaches one device via dedicated SSH identity + strict known-hosts + fixed bridge command. Keep the bridge JSON-operation bounded and non-shell. Adding another device function requires an explicit advertised tool/schema and ordinary MCP policy; do not turn SSH possession into generic phone authority.


**Filesystem safety is hard infrastructure, separate from policy** (`atlas_core/sources/local.py`). The kernel uses retained root descriptors, `openat2`/`RESOLVE_BENEATH|NO_SYMLINKS|NO_XDEV` (with a descriptor-walking fallback), regular-file checks, no-overwrite `renameat2`, managed quarantine for delete/restore, drift detection and HMAC-signed listing cursors. `read_allowed`/`mutation_allowed` at enrollment are technical only — **every enrolled root exposes both operation classes; owner discretion is exclusively policy** (`SourceRuntime.reload`). Copy/move/rename/restore destinations are normalized into the governed payload before hashing.

**Host operations use narrow deterministic OS boundaries** (`atlas_core/host.py`). Atlas user-service ops target exact `.service` units via `systemctl --user` / `journalctl --user-unit`; read-only system-service inspection is a separate capability using the system manager so units such as `tailscaled.service` are never inferred from the user manager. There is no restart-arming env flag and no Polkit layer; start/stop/restart authority is only runtime policy (seeded `CONFIRM`). Host package inspection uses `dpkg-query`/`apt-cache`; package install/remove and APT metadata refresh are exact capabilities, never arbitrary shell, and require a separately installed root-owned `atlas-package-broker` reached over a root-owned Unix socket only after runtime `CONFIRM`. The broker accepts only the Atlas service UID, validates exact Debian package names and only invokes fixed `apt-get` argument vectors against configured APT sources. This preserves the API service `NoNewPrivileges=yes` boundary; do not replace the broker with sudo or weaken that service hardening. Host filesystem paths are `realpath(strict=True)`-canonicalized before policy so a symlink alias cannot select a weaker row.

**Models are replaceable.** Providers (`openai_compatible`, `openai`, `anthropic`, `gemini`) are runtime config; credentials live only in the encrypted `CredentialStore` (AES-GCM, group-private `secrets/`) and never appear in public provider state. Decrypted model keys are passed directly to in-process provider adapters and must never be copied into process-global environment variables where Atlas-spawned subprocesses could inherit them. Enabled providers are tried in priority order; provider failure falls through to another provider, is logged as an operational warning, and must not change product semantics or become an authority decision.

**The web is a capability layer, not an agent layer.** Stable native contracts (`web.search`, `web.read`, `web.fetch`, `web.extract`, `web.download`, `web.crawl`, and eventually rendered browser/interaction contracts) sit above replaceable search/fetch/browser providers. Every invocation crosses `CapabilityRuntime` and `ActionRuntime`. Provider-native streams must cross a deterministic translation boundary before reaching reasoning: provider-specific adapters normalize search rows, and generic HTTP responses become versioned Atlas evidence contracts by media type. Raw transport bytes, scripts, presentation markup and provider-specific envelopes are not model context; legitimate source wording remains intact inside `data_only` evidence. This is evidence containment, not authority enforcement: only runtime policy may authorize an action, change a decision or widen a scope. Direct HTTP permits only HTTP(S) public-network targets, pins requests to validated public DNS answers, canonicalizes and independently validates every redirect target, forbids HTTPS downgrade, strips credentials/cookies/API-key headers when an origin changes, and enforces byte/time limits. Response headers must never be reused as subsequent request headers. Same-origin crawl must not widen authority through discovered links. `web.browser.render` is the read-only rendered fallback. Static-read quality is evaluated deterministically; `dynamic_suspected`/empty static evidence may escalate to render without another model decision, but the render invocation still crosses runtime policy. Browser network is forced through Atlas controlled transport, non-GET/HEAD requests are blocked, executable DOM state is not model context, and challenge/consent controls are reported rather than bypassed. Forms/interaction must not be exposed without an explicit tightly governed mutation contract.

**Context is assembled, not accumulated.** Chat sends a bounded capability shortlist (the model may request a wider search), relevant durable Memory and Knowledge (separate canonical stores and retrieval responsibilities) and recent turns — not the whole history. Unmatched capability discovery falls back only to the small core signpost set; never substitute an alphabetical registry slice. Tool-result bounding must preserve capability/status/trust envelopes and omit older results before corrupting the newest result into an unparseable raw tail. Memory is owner-turn-grounded and reconciled after the reply through the ordinary governed `memory.remember`, `memory.update` and `memory.retract` capabilities; there is no omnibus capture authority. Blocked/failed actions are grounded from the durable occurrence, not model speculation.

**Retrieval state is derived, versioned machinery.** Capability discovery uses `CapabilityRetriever`: exact/tool-name matches + weighted sparse matching + FastEmbed dense vectors fused by reciprocal-rank fusion over documents derived only from the live registry. Memory V2 uses the same principle with FTS5 + `sqlite-vec` vector generations. Embedding provider/model revision/package/dimensions/normalization/representation version are explicit identity; a change rebuilds derived search state, not canonical rows. The current dense implementation applies to Memory V2 and capability discovery. Knowledge passage retrieval is still FTS-based; do not claim OEM/Knowledge vector retrieval is implemented until it actually is.

**`atlas-work.db` has one connection contract.** Every participant named by `WORK_DATABASE_PARTICIPANTS` receives `WorkDatabase`; those stores must not construct their own SQLite connections. The boundary enforces row factory, foreign keys, busy timeout, `synchronous=FULL`, WAL and the qualified `sqlite-vec` extension. This is load-bearing for cross-store transactions such as memory purge and for derived vector state.

**Memory V2 is canonical now, legacy memory is not silently migrated.** Live owner memory is `memory_v2_items` plus derived FTS/vector generations. The older `memory_items` schema may still contain preserved historical rows and is accessible only through the explicit `LegacyMemoryStore` adapter. Do not silently copy/import legacy memory at startup; if migration is required, make it an explicit, tested, idempotent operation with clear provenance and rollback semantics.


**UI renders runtime truth.** Companion's default `/chat` owner surface combines conversation with Active Work, Cadence, Needs you and Control awareness; Work/Sources/Memory/Operations/Control are deep routed views rather than equal permanent top-nav products. `/atlas` remains the technical control surface for runtime, policies, models, connections, filesystem and capabilities. The UI must never invent a parallel status, permission or configuration model. Capability snapshots must resolve against one bounded read of the owner's latest policy rules/revision, not perform a fresh SQLite policy scan per capability. Installed-PWA manifest/icons/theme are part of this interface contract; do not resurrect the legacy gold startup identity.

**Persistence is separate from the checkout.** Production instance root is `/home/jaco/Projects/atlas-agent-state/production` (external to Git): `atlas-identity.db` (identity, providers, MCP servers, source roots, append-only policy), `atlas-work.db` (work, steps, occurrences, evidence, Knowledge and first-class Memory), `atlas-chat.db`, `atlas-cadence.db`, `secrets/`. Memory and action occurrences are intentionally co-located so governed `memory.purge` can delete the supersession chain and redact retained application-level action/evidence content in one SQLite transaction. Chat remains a separate database and is explicitly outside that atomic guarantee. A code deploy or rollback must never implicitly replace runtime state. Companion authentication also requires a stable configured session secret; never replace it with a process-generated value that invalidates sessions on restart.

**Morning is an external product.** It is absent from this repository, runtime, DB and UI, and served separately (its own vhost/port behind Caddy). Do not embed, re-embed, or add hidden coupling. Any future link is an explicit external API/MCP boundary. `tests/test_repository_boundaries.py` enforces absence.

**Defend simplicity.** No second authority/policy/agent/confirmation engine. Delete obsolete machinery rather than documenting around it; prefer coherent implementation over compatibility archaeology.

## Working in this repo

- Python 3.12 only; deps via `uv sync --frozen`. Prefer the existing small schema-validation implementation unless a real requirement justifies replacing or expanding it.
- Match the surrounding style and avoid unrelated reformatting; current formatting quirks are implementation details, not architectural invariants.
- New consequential behavior must route through a capability + `ActionRuntime`. Never add a side-effecting path that skips policy resolution.
- Do not run blocking model/provider, capability, Work, MCP-refresh or confirmation-continuation work directly on Starlette's asyncio event loop; dispatch it through the worker threadpool.
- Non-fatal background/fallback failures may not disappear silently: log them without including secrets or durable private content.
- New capabilities: define in a `CapabilityRegistration` with a deterministic `resolve_scope`, a `scope_hint` for the snapshot, and (if it needs the owner) `requires_owner_context`. Trusted owner context is bound from the persisted occurrence principal only at executor resolution; never inject it into the normalized, stored or hashed action payload. Seed a visible default policy row in `AtlasRuntime.seed_policy` when appropriate — default-deny means an unseeded operation is silently `NO`.
- Never return secrets to the browser or logs; never print credential-store contents.

## Verification before publishing

```
uv run pytest -q
uv run python -m compileall -q atlas_api atlas_core atlas_providers tests
cd companion && npm run lint && npm run test && npm run build
git diff --check
```

CI (`.github/workflows/tests.yml`) runs the same on push/PR to `main`. A green build is necessary but not sufficient for production: live cutover also verifies the real `atlas` user manager, persisted production state, HTTPS Companion, provider/MCP availability and self-restart reconciliation. Do not weaken a test to get green.

## Governing documents

`Atlas Constitution.md` (product principles) · `Atlas Product Definition.md` (responsibilities/boundary) · `Atlas Architecture — Runtime and Topology.md` (implemented runtime) · `Atlas Interface Design.md` (Companion visual and interaction doctrine). When prose and implementation disagree, the implementation and tests are the truth — name the discrepancy and correct the prose.
