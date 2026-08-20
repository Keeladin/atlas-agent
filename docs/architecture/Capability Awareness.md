# Capability awareness

**Status:** Foundation only. No mode enforcement, UI, or persistence.  
**Canonical type:** `CapabilitySpec` — this is not a second ontology.

## Invariant

**Discovery never equals exposure.** Binding is not permission. A tool is not a capability.

```text
Discovery
    ↓
Provider inventory          (N8NMCPProvider)

Capability
    ↓
Atlas meaning               (CapabilitySpec)

Binding
    ↓
Deployment implementation   (CapabilityBinding)

Tool
    ↓
Runtime executable          (ToolDescriptor)

ToolGateway
    ↓
Execution enforcement       (unchanged invoke path)
```

Unmapped provider tools remain inventory only.

Replacing `n8n.execute_workflow` with `temporal.run_workflow` changes only the binding. Capability id, prompts, `required_authority`, and `confirmation` stay.

## What was reused

| Existing | Role |
|---|---|
| `CapabilitySpec` | Stable Atlas meaning |
| `CapabilitySpec.allowed_tools` | Runtime execution-frame ToolDescriptor allow-list. Not vendor identity. |
| `CapabilitySpec.required_authority` | Class of action this work item must be granted |
| `CapabilitySpec.side_effects` | Named side-effect labels used by current runtime |
| `CapabilityRegistry` / `CapabilityRegistration` | Spec + handler record. Not a deployment binding. |
| `ToolGateway` | Invoke boundary |
| `N8NMCPProvider` | External MCP inventory |

## CapabilitySpec (meaning only)

Optional fields with defaults so existing specs stay valid:

- `side_effect_class` — `none` / `reversible` / `irreversible` / `external_effect`
- `confirmation` — `none` (default) or `required` (action property; **not** authority approval)

The spec does **not** encode n8n, MCP, vendor tool names, provider selection, or mode permissions.

**Approval** remains the existing runtime concept: “May this work item perform this class of action?” (authority escalation). Confirmation is “Should this exact execution happen now?” Confirmation is not executed yet.

## CapabilityBinding (deployment)

`CapabilityBinding` lives off the spec:

```text
capability_id:  automation.workflow.execute
provider:       n8n
implementation: execute_workflow
version:        1
```

`CapabilityBindingIndex` stores these in memory. Binding does not grant authority. A capability may have zero bindings. Several providers may bind one capability.

`allowed_tools` must not become `mcp.n8n.execute_workflow` on the capability identity.

## Exposure policy (placeholder)

`ExposurePolicy` / `CapabilityExposure` remain unenforced per-mode data (`explain | brief | execute | hidden`). Mode flags are not stored on `CapabilitySpec`.

## What this does not do

- enforce exposure or confirmation
- filter model context
- auto-map n8n tools
- add SQLite tables
- change ToolGateway.invoke
- register `automation.workflow.execute` as a builtin runtime capability
