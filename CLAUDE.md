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

**Confirmation is an execution state, not a second permission system** (`ActionRuntime`, `atlas_core/actions/`). A `CONFIRM` creates a durable `pending_confirmation` `ActionOccurrence` binding capability, normalized operation, normalized resource scope, canonical payload SHA-256 and principal. Confirmation is accepted only from the same authenticated principal, expires after `CONFIRM_MAX_AGE` (5 min), and **policy is re-resolved immediately before execution** — a current `NO` blocks with `policy_revoked_before_execution`. The stored payload is immutable (no update path; `confirm` takes no client payload), so there is no payload-mutation-after-confirmation vector. State transitions are compare-and-swap; a replayed confirm hits a non-pending status and fails.

**Deterministic work stays deterministic.** Parsing, scope/payload normalization, hashing, policy resolution, persistence, filesystem containment and verification are code, not model calls. Do not ask a model to decide what code can decide exactly.

**Evidence and verification precede completion.** Consequential actions produce durable occurrences, receipts and evidence rows. A model asserting success is not evidence. Verifiable outcomes stay `uncertain` until reconciled — self-restart enters `uncertain` and the successor process reconciles via `INVOCATION_ID` (`HostRuntime.reconcile_self_restart`), never the predecessor claiming to have observed its own replacement.

**Work must not freeze authority.** `WorkItem` stores an objective and ordered capability steps, never a scalar authority allowance. Each step crosses `CapabilityRuntime` → policy **when it actually runs**, so a policy change after Work was created still governs the eventual side effect. `CONFIRM` pauses Work (`waiting_confirmation`); a later `NO` fails the step. Cadence materializes recurring duties as ordinary Work with no privileged path around policy.

**MCP/n8n discovery populates inventory, not permission.** Every advertised tool of an enabled server is registered under scope `mcp/<server-id>/tool/<tool-name>` (operation `invoke`). `n8n` kind changes metadata, not authority. Atlas supports Streamable HTTP and locally spawned stdio MCP transports. The technical transport reaches a tool only after the policy gate; `MCPRuntime.call_tool` is post-gate transport and must never be called to skip it.

**Do not rebuild a semantic mail layer.** Google Workspace is an external capability provider: Google Discovery defines the Gmail, Drive and Calendar API method surface, the provider exposes it as MCP tools, and Atlas policy governs those exact tools. Do not add parallel `mail.read`, `mail.send`, `mail.modify` aliases or a handcrafted Gmail whitelist. Provider ergonomics may improve presentation, but must not create a second executable authority path for the same external action. Keep provider OAuth/config state outside the Git checkout; technical provider credentials remain capability/custody, never owner policy.

**Filesystem safety is hard infrastructure, separate from policy** (`atlas_core/sources/local.py`). The kernel uses retained root descriptors, `openat2`/`RESOLVE_BENEATH|NO_SYMLINKS|NO_XDEV` (with a descriptor-walking fallback), regular-file checks, no-overwrite `renameat2`, managed quarantine for delete/restore, drift detection and HMAC-signed listing cursors. `read_allowed`/`mutation_allowed` at enrollment are technical only — **every enrolled root exposes both operation classes; owner discretion is exclusively policy** (`SourceRuntime.reload`). Copy/move/rename/restore destinations are normalized into the governed payload before hashing.

**Host operations use the `atlas` user-systemd boundary** (`atlas_core/host.py`). Service ops target exact `.service` units via `systemctl --user` / `journalctl --user-unit`. There is no restart-arming env flag and no Polkit layer; start/stop/restart authority is only runtime policy (seeded `CONFIRM`). Host filesystem paths are `realpath(strict=True)`-canonicalized before policy so a symlink alias cannot select a weaker row.

**Models are replaceable.** Providers (`openai_compatible`, `openai`, `anthropic`, `gemini`) are runtime config; credentials live only in the encrypted `CredentialStore` (AES-GCM, group-private `secrets/`) and never appear in public provider state. Enabled providers are tried in priority order; provider failure falls through to another provider and must not change product semantics or become an authority decision.

**Context is assembled, not accumulated.** Chat sends a bounded capability shortlist (the model may request a wider search), relevant durable knowledge (SQLite FTS) and recent turns — not the whole history. Blocked/failed actions are grounded from the durable occurrence, not model speculation.

**UI renders runtime truth.** Companion (`/atlas` control surface: runtime, policies, models, connections, filesystem, capabilities) reads and edits the same runtime used for execution. It must never invent a parallel status, permission or configuration model.

**Persistence is separate from the checkout.** Production instance root is `/home/jaco/Projects/atlas-agent-state/production` (external to Git): `atlas-identity.db` (identity, providers, MCP servers, source roots, append-only policy), `atlas-work.db` (work, steps, occurrences, evidence, knowledge), `atlas-chat.db`, `atlas-cadence.db`, `secrets/`. A code deploy or rollback must never implicitly replace runtime state.

**Morning is an external product.** It is absent from this repository, runtime, DB and UI, and served separately (its own vhost/port behind Caddy). Do not embed, re-embed, or add hidden coupling. Any future link is an explicit external API/MCP boundary. `tests/test_repository_boundaries.py` enforces absence.

**Defend simplicity.** No second authority/policy/agent/confirmation engine. Delete obsolete machinery rather than documenting around it; prefer coherent implementation over compatibility archaeology.

## Working in this repo

- Python 3.12 only; deps via `uv sync --frozen`. Prefer the existing small schema-validation implementation unless a real requirement justifies replacing or expanding it.
- Match the surrounding style and avoid unrelated reformatting; current formatting quirks are implementation details, not architectural invariants.
- New consequential behavior must route through a capability + `ActionRuntime`. Never add a side-effecting path that skips policy resolution.
- New capabilities: define in a `CapabilityRegistration` with a deterministic `resolve_scope`, a `scope_hint` for the snapshot, and (if it needs the owner) `requires_owner_context`. Seed a visible default policy row in `AtlasRuntime.seed_policy` when appropriate — default-deny means an unseeded operation is silently `NO`.
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

`Atlas Constitution.md` (product principles) · `Atlas Product Definition.md` (responsibilities/boundary) · `Atlas Architecture — Runtime and Topology.md` (implemented runtime). When prose and implementation disagree, the implementation and tests are the truth — name the discrepancy and correct the prose.
