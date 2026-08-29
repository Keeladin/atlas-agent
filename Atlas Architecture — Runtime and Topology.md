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
6. knowledge, Work and Cadence;
7. Chat over the resulting live capability inventory.

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

## 6. Capability registry

`CapabilityRegistry` contains the complete runtime inventory. A registration consists of:

- a semantic capability definition;
- JSON input schema;
- deterministic scope resolver;
- executor;
- technical availability function;
- metadata such as provider, source or scope hint.

Definitions classify effect (`none`, `internal`, `reversible`, `external`, `destructive`) but do not encode owner permission.

`CapabilityRuntime.invoke()` validates input, resolves the exact scope and normalized payload, adds required owner execution context, and submits the result to `ActionRuntime`.

## 7. MCP and n8n

MCP servers are runtime-managed persistent connections. Enabling or refreshing a server discovers every advertised tool and registers it under the server's capability namespace. Atlas supports both Streamable HTTP MCP and locally spawned stdio MCP providers. Transport changes connection mechanics, not policy semantics.

n8n uses the same generic MCP mechanism. A server being of kind `n8n` changes inventory metadata, not authority semantics.

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

## 13. Knowledge and Chat

Knowledge is durable owner context stored separately from conversation history and searchable with SQLite FTS.

Chat persists conversations and turns. Relevant durable knowledge and recent turns are assembled for each model decision rather than treating the full chat history as the runtime state.

Tool outcomes are fed back into the conversational turn. Blocked and failed actions are grounded from the durable occurrence rather than rewritten as model speculation.

## 14. Persistence topology

The production instance root is external to the Git checkout. This prevents code deployment, rollback or repository replacement from implicitly replacing runtime state.

```text
atlas-identity.db
  principals, connections, service bindings, provider settings,
  MCP servers, source roots, append-only policy events

atlas-work.db
  Work, steps, governed action occurrences, evidence, knowledge

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

The Atlas screen exposes live policy, providers, MCP/n8n connections, external-account bindings, source roots, host state and capability inventory from the same runtime used for execution.

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

The acceptance suite attacks the governing invariants: policy specificity/default deny, exact confirmation and policy recheck, Work recheck, filesystem containment, Streamable HTTP/stdio MCP inventory and dispatch, provider discovery, user-systemd dispatch, API authentication/control and deployment-state assumptions.

Companion has independent lint, unit tests and a production/PWA build.

A green build is necessary but not sufficient for deployment. Live cutover also verifies the actual `atlas` user manager, persisted production state, HTTPS Companion, provider/MCP availability and self-restart reconciliation.
