# Atlas

Atlas is a single persistent operational agent for durable personal work.

The agent is the runtime. Models, providers, MCP servers, n8n workflows, filesystem access and host operations are capabilities that Atlas can use; they are not separate agent identities.

The current implementation is a clean runtime built around one governing boundary:

```text
intent
  -> capability
  -> deterministic scope resolution
  -> owner runtime policy
  -> NO | YES | CONFIRM
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
- durable Work and recurring Cadence;
- an append-only owner policy with literal `NO`, `YES` and `CONFIRM` decisions;
- durable exact-action confirmations bound to the normalized payload hash;
- full discovered MCP and n8n capability inventory;
- generic MCP over Streamable HTTP and local stdio transports;
- a Discovery-driven Google Workspace provider surface for Gmail, Drive and Calendar;
- runtime-managed model providers and encrypted credentials;
- hardened enrolled filesystem roots;
- host observation and user-systemd service operations;
- durable knowledge / memory;
- action receipts, evidence and restart reconciliation;
- the Companion PWA and runtime-control surface.

## Authority

Capability is not authority. Provider configuration, systemd, MCP credentials and source enrollment make operations technically available; they do not grant Atlas permission to perform them.

Owner discretion lives only in the running policy store. A policy change takes effect on the next resolution without a service restart, redeploy or provider reconfiguration.

The semantics are exact:

- `NO`: do not execute;
- `YES`: execute;
- `CONFIRM`: create a durable exact-action occurrence and wait for the authenticated owner;
- no matching policy row: `NO`.

Policy is resolved after the resource scope and payload are normalized. `CONFIRM` is re-resolved immediately before execution, so a later `NO` stops the action. Work steps cross the same gate when they actually run.

Policy history is append-only. More-specific resource scopes override broader scopes. Explicit sensitive-child rules are visible policy rows, not a hidden deny layer.

Authentication, provider attestation, schema validation, filesystem containment and other technical constraints remain execution invariants rather than competing permission systems.

## Capability inventory

`CapabilityRegistry` is the complete runtime inventory. Registration and discovery never imply permission.

Native capabilities include knowledge, Work, Cadence, local sources and host operations. Every tool advertised by an enabled MCP server is registered dynamically. n8n is treated as an MCP-backed capability provider and is not restricted to a handcrafted subset. MCP transport may be Streamable HTTP or a locally spawned stdio provider.

Google Workspace is integrated as a provider rather than a mail subsystem: Google Discovery defines the Gmail, Drive and Calendar method surface, the provider exposes those methods through MCP, and Atlas policy governs each discovered tool. Atlas does not maintain a parallel `mail.read` / `mail.send` semantic API. The provider keeps its `gws` OAuth/config state under the external production-state tree rather than inside the Git checkout.

The conversational model selects capabilities and arguments. It does not decide whether the operation is allowed and cannot manufacture confirmation.

## Persistence

Production state is separate from the code checkout. The deployed instance root is:

```text
/home/jaco/Projects/atlas-agent-state/production
```

The fresh stores are:

```text
atlas-identity.db   identity, provider settings, MCP servers, source roots, policy
atlas-work.db       work, steps, action occurrences, evidence, knowledge
atlas-chat.db       conversations and chat turns
atlas-cadence.db    recurring cadence definitions
secrets/            encrypted credential database and master key
```

Old Atlas Work, Chat, Cadence, HostAction, authority-grant and Morning databases are not reused by this runtime.

### Google Workspace provider state

The production Google Workspace provider is a stdio MCP provider, not an Atlas mail subsystem. Its external runtime files live under the production state root:

```text
bin/gws                         current Google Workspace CLI binary
google-workspace/config/        gws OAuth/config state
google-workspace/workspace/     bounded provider upload/download workspace
```

The registered `google-workspace` MCP server launches `atlas_providers.google_workspace_mcp`, which derives Gmail, Drive and Calendar tools from Google Discovery and dispatches them through the current `gws` binary. OAuth/config state is technical provider custody; Atlas `NO` / `YES` / `CONFIRM` policy remains the sole discretionary authority.

## Companion

The primary Companion navigation is:

```text
Chat -> Work -> Sources -> Atlas
```

`Atlas` is the final control surface. It shows and edits the live runtime configuration: policy, providers, MCP/n8n servers, external account bindings, source roots, host state, pending confirmations and capability inventory.

There is no `Now` page and no Morning UI.

Secrets are never returned to the browser. The UI can replace credentials, but the server stores them only in the encrypted credential boundary.

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

Host service capabilities use `systemctl --user`. Runtime policy, not systemd, decides whether status, logs, start, stop or restart are `NO`, `YES` or `CONFIRM`.

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

`instance/companion-auth.env` must contain the Companion authentication configuration before login can be used.

## Repository layout

```text
atlas_core/
  actions/        canonical action/confirmation gate
  capabilities/   inventory, schemas and scope resolution
  policy/         append-only NO/YES/CONFIRM policy
  identity/       principals, account connections and technical bindings
  providers/      model contracts, HTTP adapters and runtime settings
  mcp/            generic MCP/n8n discovery and dispatch
  mcp_stdio.py     generic local stdio MCP transport
  sources/        hardened local filesystem kernel
  work/           durable work and steps
  cadence/        recurring duties
  chat/           conversational orchestration
  host.py         host observation and user-systemd capabilities
  knowledge.py    durable knowledge and memory
  secrets.py      encrypted host-local credential store
atlas_api/        authenticated Starlette composition/control plane
atlas_providers/  external capability-provider adapters (Google Workspace)
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

When documentation and implementation disagree, inspect the implementation and tests and correct the documentation. Runtime truth wins over stale architectural prose.
