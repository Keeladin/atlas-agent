# Capability awareness

**Status:** Current capability identity model.  
**Canonical type:** `CapabilityDefinition` — product meaning. Not an implementation and not a permission.

Related: [Security and Intent Model](./Atlas%20Security%20and%20Intent%20Model.md), [Capability Exposure and Mode Interaction](./Capability%20Exposure%20and%20Mode%20Interaction.md).

## Ownership

Atlas knows capabilities independently of whether this deployment can execute them.

```text
CapabilityDefinition
        |
        | meaning
        v

CapabilityExecutionProfile
        |
        | deployment
        v

CapabilityRegistration
        |
        | executable Work implementation
        v

TaskRuntime
```

| Type | Owns | Does not own |
|---|---|---|
| `CapabilityDefinition` | Capability identity (`id`, description, `required_authority`, `confirmation`, `side_effect_class`) | Tools, handlers, providers, MCP, n8n, availability |
| `CapabilityExecutionProfile` | This deployment's way to perform one catalog id (binding, tools, verifier, version, budgets) | Product meaning |
| `CapabilityRegistration` | Work-engine record: definition + profile + handler | Chat/Advanced identity |
| `TaskRuntime` | Durable Work execution | Capability identity |

`catalog()` / `lookup()` are the product identity surface. Chat and Advanced read meaning only. Work.accept reads `catalog()`. Work.run requires an available profile (and a handler for deterministic/tool/composite kinds) before `TaskRuntime` is reached.

`CapabilityAwareness` is an alias of `CapabilityDefinition`. It is not a second ontology.

## Invariants

```text
Discovery ≠ Capability

Capability ≠ Implementation

Capability ≠ Permission

Authority ≠ Confirmation

Intent ≠ Execution

Execution ≠ Verification

Tool ≠ Capability

Binding ≠ Permission
```

A tool is not a capability. A handler is not a capability. An MCP discovery is not a capability.

## What Atlas may explain

Chat may know that workflow execution exists without receiving `execute_workflow()`, `publish_workflow()`, or vendor tool names.

```text
Discovery
    ↓
Provider inventory          (N8NMCPProvider → ToolDescriptor)

Capability
    ↓
Atlas meaning               (CapabilityDefinition / catalog())

Binding / profile
    ↓
This deployment             (CapabilityBinding, CapabilityExecutionProfile)

Registration
    ↓
Work engine                 (CapabilityRegistration)

ToolGateway
    ↓
Invoke under authority      (unchanged fail-closed path)

TaskRuntime
    ↓
Verified Work execution
```

Unmapped provider tools remain inventory only.

Replacing `n8n.execute_workflow` with `temporal.run_workflow` changes the profile/binding. Capability id, prompts, `required_authority`, and `confirmation` stay on `CapabilityDefinition`.

## Types

| Type | Role |
|---|---|
| `CapabilityDefinition` | Stable Atlas meaning |
| `CapabilityExecutionProfile.tools` | Runtime-frame ToolDescriptor allow-list. Not vendor identity. |
| `CapabilityDefinition.required_authority` | Class of action this work item must be granted |
| `CapabilityExecutionProfile.side_effects` | Named side-effect labels used by current Work runtime |
| `CapabilityRegistry` / `CapabilityRegistration` | Executable Work set. Not the meaning catalog. |
| `ToolGateway` | Invoke boundary. Has no `capability()` identity map. |
| `N8NMCPProvider` | External MCP inventory |

### CapabilityDefinition (meaning only)

- `id`
- `description`
- `required_authority`
- `confirmation` — `none` or `required` (action property; **not** authority approval)
- `side_effect_class` — `none` / `reversible` / `irreversible` / `external_effect`

The definition does **not** encode n8n, MCP, vendor tool names, provider selection, mode permissions, tools, handlers, or verifiers.

**Approval** remains the existing runtime concept: “May this work item perform this class of action?” (authority escalation). Confirmation is “Should this exact execution happen now?” Confirmation is not executed yet.

### CapabilityExecutionProfile (deployment)

How this deployment performs one catalog id. `available` is true when a binding is present, or when `executor_kind` is `model` or `human`. Absence of a profile does not remove the definition from `catalog()`.

### CapabilityBinding (implementation pointer)

```text
capability_id:  automation.workflow.execute
provider:       n8n
implementation: execute_workflow
version:        1
```

Binding does not grant authority. A capability may have zero bindings. Several providers may bind one capability. Bindings must not become `mcp.n8n.execute_workflow` on the capability identity.

## What may not mint identity

| Path | Classification |
|---|---|
| `catalog()` / `lookup()` / `require()` | Product identity. Only `catalog()` constructs `CapabilityDefinition`. |
| `DeploymentInventory.register` | Deployment only. `WorkRuntime` skips unknown catalog ids. |
| `CapabilityRegistry.register` | Work execution record. Does not add catalog rows. Production bootstrap passes `require(id)`. |
| Builtin / knowledge / morning registration | Execution profiles + handlers against `require(id)`. They do not construct definitions. |
| `ToolGateway.register` | Tool inventory |
| MCP `register_discovered` | Tool inventory (`mcp.*` ids). Not definitions. |
| `CapabilityBinding` | Implementation pointer |

`CapabilitySpec` is removed. It has no owner. Do not restore it as a compatibility alias.

## Exposure policy (placeholder)

`ExposurePolicy` / `CapabilityExposure` remain unenforced per-mode data (`explain | brief | execute | hidden`). Mode flags are not stored on `CapabilityDefinition`.

## What this does not do

- auto-map n8n tools to catalog ids
- add SQLite tables
- change ToolGateway.invoke
- reconnect Companion to Chat/Advanced/Work
- restore `build_runtime()` as product identity
