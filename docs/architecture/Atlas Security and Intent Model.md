# Atlas Security and Intent Model

**Status:** Current capability / authority / confirmation split.  
**Scope:** Separation of capability awareness, authority, confirmation, and execution  
**Does not:** reconnect Companion, auto-map MCP tools, or treat discovery as identity

Atlas is a personal operational agent. It must neither pretend capabilities do not exist nor execute unintended external actions.

This note names the concepts that must stay distinct so later control-plane and WORK-mode work does not collapse them.

Related: [Capability Awareness](./Capability%20Awareness.md), [Capability Exposure and Mode Interaction](./Capability%20Exposure%20and%20Mode%20Interaction.md), [Runtime Governance](./Atlas%20Runtime%20Governance%20Reconciliation.md).

---

## Layers

```text
Capability     Stable Atlas concept          (CapabilityDefinition / catalog())
Profile        This deployment               (CapabilityExecutionProfile)
Registration   Executable Work record        (CapabilityRegistration)
Binding        Implementation pointer        (CapabilityBinding)
Tool           Runtime executable            (ToolDescriptor / ToolGateway)
Provider       External/internal integration (N8NMCPProvider, APIs, internal code)
Policy         Future decision layer         (exposure; not on the definition)
```

Replacing `n8n.execute_workflow` with `temporal.run_workflow` must not change capability id, prompts, authority rules, or confirmation rules.

Invariants:

```text
Discovery != Capability

Capability != Implementation

Capability != Permission

Binding != Permission

Authority != Confirmation

Intent != Execution

Execution != Verification

Tool != Capability
```

---

## 1. Capability

A **capability** describes an action Atlas understands as a **product concept**. It is independent of implementation.

Examples:

```text
communication.email.send
automation.workflow.execute
knowledge.search
```

A capability is **not**:

- an MCP tool
- a provider
- permission
- execution authority
- a confirmation of a specific payload

Canonical type today: `CapabilityDefinition`. It holds Atlas meaning: id, description, `side_effect_class`, `required_authority`, and `confirmation` (`none` | `required`). It does not hold n8n, MCP tool names, provider selection, tools, handlers, or mode permissions.

`CapabilityExecutionProfile` is deployment availability: binding, tools, verifier, version, budgets. `allowed_tools` on a Work runtime frame is a ToolDescriptor allow-list from that profile. It is not capability identity and must not become a vendor tool list (`mcp.n8n.execute_workflow`).

`CapabilityRegistration` is the Work execution binding (definition + profile + handler). `WorkEngine` executes resolved pins. It does not mint catalog identity.

Discovery, policy, and invoke paths must not invent a second ontology beside `CapabilityDefinition`. `CapabilitySpec` is removed.

---

## 2. Provider / tool discovery

External tools arrive as **inventory**, not as Atlas meaning.

```text
MCP Discovery
      |
      v
Provider Inventory
      |
      v
Capability Binding
```

Rules:

- Discovery does not create capabilities.
- New MCP tools do not automatically become available to Atlas reasoning.
- Unmapped tools remain provider inventory.
- Binding is explicit and reviewed.

Example: n8n instance-level MCP may inventory:

```text
n8n:
    execute_workflow
    publish_workflow
    list_credentials
```

That does **not** automatically create:

```text
Atlas:
    workflow.execute
    workflow.publish
    credential.manage
```

**Discovery != exposure.** A tool sitting on a provider gateway is not a capability the model should plan with.

A **CapabilityBinding** is deployment data:

```text
capability_id:  automation.workflow.execute
provider:       n8n
implementation: execute_workflow
version:        1
```

Binding does not grant authority. A capability may exist with zero bindings. Several bindings may implement one capability.

---

## 3. Authority

**Authority** answers:

```text
May this work item perform this class of action?
```

Example:

```text
Capability:
    communication.email.send

Required authority:
    communicate
```

Authority belongs to the **work / task context** (`authority_scope` on the work item, `required_authority` on the capability). ToolGateway remains the last fail-closed check for that class of action.

Authority does **not** mean:

- the operator approved this specific action
- the action should happen automatically
- whoever is currently talking to Atlas may do anything in that class forever
- the whole task is globally elevated when one step is approved

Insufficient standing grant may still pause work for an **authority elevation** of that bounded step. That pause is not confirmation of a payload.

---

## 4. Confirmation (future concept)

Do not reuse Atlas **approval**. That word already means **authority escalation**:

```text
Authority approval:
    May this work item perform this class of action?
```

**Confirmation** answers:

```text
Should Atlas execute this exact action now?
```

It is an action property (`confirmation: none | required`), not an invoke flag and not an authority grant. The execution flow is not implemented in this document.

Example:

```text
Send email:
    Recipient: John
    Subject: Maintenance Report
    Body hash: xxxx

Confirmation:
    required
```

Rules:

- Confirmation is bound to the **specific payload** (recipients, body hash, workflow id, execution mode, …).
- Confirmation does not grant authority.
- Authority does not imply confirmation.
- A conversational “yes” is not confirmation.
- A model-supplied `confirmed=true` flag is not confirmation.
- Unattended triggers (schedules, webhooks) are not confirmation of a chat intent.

Even a single-user personal agent needs this split. Talking to Atlas is not the same as authorizing this send.

When implemented, confirmation should be a durable pause bound to the payload, not an invoke-time boolean. It must not raise the work item’s authority scope.

---

## 5. Execution pipeline

```text
Intent
  |
  v
CapabilityDefinition
  |
  v
Authority
  |
  v
Confirmation
  |
  v
CapabilityExecutionProfile
  |
  v
CapabilityRegistration
  |
  v
WorkEngine / ToolGateway
  |
  v
Provider
  |
  v
Evidence / Verification
```

| Stage | Question |
|---|---|
| Intent | What does the operator want? |
| Capability | What Atlas product action is that? (`CapabilityDefinition`) |
| Authority | May this work item do that *class* of action? |
| Confirmation | May *this payload* run now? |
| Profile | Can this deployment perform it? (`CapabilityExecutionProfile`) |
| Registration | Work handler + profile for `WorkEngine` |
| ToolGateway | Invoke the bound implementation under constraints |
| Provider | MCP / API / internal code |
| Evidence / Verification | Did it actually happen? A model sentence is not completion |

Chat and Advanced must not jump from intent to ToolGateway. Work is the execution context. Companion is not reconnected to those composition roots in this model.

---

## 6. Design invariants

```text
Discovery != Capability

Capability != Implementation

Capability != Permission

Binding != Permission

Authority != Confirmation

Tool != Capability

Intent != Execution

Execution != Verification
```

Also:

- Awareness is not a raw tool catalog.
- Side-effect class informs defaults; it is not itself confirmation policy.
- ToolGateway enforces execution; it does not define Atlas meaning.
- MCP is transport and inventory, not ontology.
- Handler registration does not create `catalog()` identity.

---

## 7. Future control plane

Conceptual only. No UI in this change.

A future control panel should manage:

- capabilities (Atlas product actions)
- bindings (which provider tool implements a capability)
- exposure policies (CHAT explain / ADVANCED brief / WORK execute)
- confirmation policies (which capabilities need payload-bound consent)

It should **not** directly toggle provider tools.

Avoid:

```text
Enable n8n tool X
Disable n8n tool Y
```

Prefer:

```text
Enable workflow execution capability
Bind implementation to n8n
Require confirmation
```

Unmapped n8n tools stay off the control plane until someone writes a capability and a binding.

---

## What this is not

- Not enterprise IAM (no users, roles, or ACLs in this model).
- Not a change to current SQLite task state.
- Not enforcement of confirmation or exposure in WorkEngine.
- Not a reason to hide capabilities from awareness; hide **unauthorized handles**, not **product meaning**.
