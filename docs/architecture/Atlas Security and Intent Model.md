# Atlas Security and Intent Model

**Status:** Foundation document only. Not runtime behaviour.  
**Scope:** Separation of capability awareness, authority, confirmation, and execution  
**Does not:** change code, schema, ToolGateway, MCP, or current enforcement

Atlas is a personal operational agent. It must neither pretend capabilities do not exist nor execute unintended external actions.

This note names the concepts that must stay distinct so later control-plane and WORK-mode work does not collapse them.

Related: [Capability Awareness](./Capability%20Awareness.md), [Runtime Governance](./Atlas%20Runtime%20Governance%20Reconciliation.md).

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

Canonical type today: `CapabilitySpec`. Discovery, policy, and invoke paths must not invent a second ontology beside it.

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

**Confirmation** answers:

```text
Should Atlas execute this exact action now?
```

It is a separate future concept from authority. It is not implemented in this document.

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
Capability
  |
  v
Authority
  |
  v
Confirmation
  |
  v
ToolGateway
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
| Capability | What Atlas product action is that? |
| Authority | May this work item do that *class* of action? |
| Confirmation | May *this payload* run now? |
| ToolGateway | Invoke the bound implementation under constraints |
| Provider | MCP / API / internal code |
| Evidence / Verification | Did it actually happen? A model sentence is not completion |

CHAT and ADVANCED_CONVERSATION must not jump from intent to ToolGateway. WORK is the execution context. This document does not wire those modes.

---

## 6. Design invariants

```text
Discovery != Capability

Capability != Permission

Authority != Confirmation

Intent != Execution

Execution != Verification
```

Also:

- Awareness is not a raw tool catalog.
- Side-effect class informs defaults; it is not itself confirmation policy.
- ToolGateway enforces execution; it does not define Atlas meaning.
- MCP is transport and inventory, not ontology.

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
- Not enforcement of `CapabilitySpec.approval` or confirmation in TaskRuntime.
- Not a reason to hide capabilities from awareness; hide **unauthorized handles**, not **product meaning**.
