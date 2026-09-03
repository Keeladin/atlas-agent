# AGENTS.md — Atlas

This repository is **Atlas v3**, a single persistent operational agent (Python 3.12 + Starlette backend, React PWA in `companion/`). The agent is the runtime; models, providers, MCP/n8n servers, filesystem roots and host operations are capabilities, not separate agents.

- **Canonical server path:** `/home/jaco/Projects/atlas-agent`
- **Production runtime state (external to the checkout):** `/home/jaco/Projects/atlas-agent-state/production` — never assume a code deploy replaces it.
- **Architecture is governed by:** `Atlas Constitution.md`, `Atlas Product Definition.md`, `Atlas Architecture — Runtime and Topology.md`, and `Atlas Interface Design.md`. **`CLAUDE.md` holds the fuller engineering invariants — read it before changing runtime behavior.**

Core rule you must not break: **capability ≠ authority.** Discretionary permission comes only from the runtime owner policy — literal `NO` / `YES` / `CONFIRM`, where a missing rule means `NO`. Every consequential action must route through a capability and the canonical `ActionRuntime` gate. Do not create a second authority, policy, confirmation or agent engine, and do not let the model self-authorize.

- **Morning is outside this product and repository.** Do not embed it, depend on its internals, or add hidden coupling; any future link is an explicit external API/MCP boundary.
- **Implementation, tests and runtime truth beat stale prose.** If a document disagrees with the code, fix the document.
- **Never expose secrets** — no credential-store contents, API keys, tokens or session secrets in responses, logs or the UI.
- **Google Workspace is a provider, not a mail subsystem.** Gmail, Drive and Calendar enter through the Discovery-driven stdio MCP provider. Do not recreate `MailRuntime`, semantic mail aliases, or a handcrafted Gmail whitelist.
- **Current retrieval is hybrid but scoped.** Memory V2 and capability discovery use local dense embeddings plus sparse retrieval/rank fusion; Knowledge passages remain FTS-based. Do not describe OEM/Knowledge retrieval as vectorized yet.
- **`atlas-work.db` uses `WorkDatabase` + sqlite-vec.** Participating stores do not open ad-hoc SQLite connections. Canonical rows are truth; search vectors/index generations are derived.
- **Legacy `memory_items` rows are preserved but not live Memory V2.** There is no implicit startup migration.
- **Android phone access is an MCP provider, not SSH authority.** The current provider is intentionally narrow: location, telephony inspection and SMS through a fixed Termux bridge.
- **Companion opens on one owner surface.** Conversation, Work/Cadence awareness and `Needs you` are co-visible; `/atlas` is deeper Control, not the whole product shell.


Before publishing changes, run and keep green (do not weaken tests):

```
uv run pytest -q
uv run python -m compileall -q atlas_api atlas_core atlas_providers tests
cd companion && npm run lint && npm run test && npm run build
git diff --check
```

