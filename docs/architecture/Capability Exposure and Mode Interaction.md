# Capability Exposure and Mode Interaction

**Status:** Design document only. Not runtime behaviour.  
**Scope:** Boundary between capability awareness, mode exposure, task intent, and execution  
**Does not:** enforce policy, change ToolGateway, MCP, schema, UI, or reconnect Companion

Related: [Capability Awareness](./Capability%20Awareness.md), [Security and Intent Model](./Atlas%20Security%20and%20Intent%20Model.md).

---

## Core question

Atlas must answer:

```text
I know this capability exists.
Should this interaction be allowed to discuss it, propose it, or execute it?
```

without making every capability a visible executable tool.

Awareness is not a syscall table. CHAT may know that workflow execution exists without receiving `execute_workflow()`, `publish_workflow()`, or `archive_workflow()`.

---

## Layers

```text
Capability
    |
    | "What Atlas understands"
    |
    v

Exposure Policy
    |
    | "What this mode may see / propose"
    |
    v

Task Brief
    |
    | "What this specific work wants to accomplish"
    |
    v

Runtime Frame
    |
    | "What tools are actually available"
    |
    v

Execution
```

| Layer | Question | Must not |
|---|---|---|
| Capability (`CapabilityDefinition`) | What product action exists? | Name n8n / MCP tools as identity |
| Exposure | May this **mode** explain, brief, or execute it? | Grant authority or confirmation |
| Task Brief | What does **this work** intend? | Invoke ToolGateway |
| Runtime frame | Which **bound tools** may this WORK frame call? | Dump the provider inventory |
| Execution | Did ToolGateway run it under authority + confirmation? | Treat a model sentence as completion |

These layers are sequential. CHAT and ADVANCED_CONVERSATION stop before the runtime frame. WORK is the execution context.

---

## Capability awareness

A capability may exist without being executable.

Example:

```text
id:             automation.workflow.execute
meaning:        Execute an automation workflow
authority:      execute_external
confirmation:   required
```

CHAT may know:

```text
Atlas supports workflow execution.
```

CHAT does **not** receive:

```text
execute_workflow()
publish_workflow()
archive_workflow()
```

Awareness is the product manifesto: Atlas meaning, class of effect, required authority, confirmation as an action property. It is not the MCP catalog and not the ToolGateway dump.

Unmapped provider tools are not part of awareness.

---

## Exposure levels

Exposure answers what **this interaction mode** may do with a capability. It is not authority and not confirmation.

| Level | Meaning |
|---|---|
| `hidden` | Capability does not exist in this context. |
| `explain` | Atlas can explain the capability. No tools. No brief required. |
| `brief` | Atlas can create a task proposal that requires WORK. No execution. |
| `execute` | Capability may participate in a runtime frame. Still subject to authority and confirmation. |

Example:

| Mode | `communication.email.send` |
|---|---|
| CHAT | `explain` |
| ADVANCED_CONVERSATION | `brief` |
| WORK | `execute` |

CHAT: “I can draft; sending needs WORK and authorization.”  
ADVANCED: emit a Task Brief for send.  
WORK: bind an implementation and, if confirmation is required, pause on the payload.

`execute` does not skip authority or confirmation. Exposure is permission to *enter the pipeline*, not to fire the provider.

This table is conceptual. Nothing in the current runtime reads or enforces it.

---

## Task Brief

ADVANCED_CONVERSATION does **not** execute. It transforms intent into a brief that WORK may accept or refuse.

Example:

```text
User:
    Send the maintenance report to management.

ADVANCED produces:

TaskBrief:
    objective:
        Send maintenance report
    capabilities:
        - communication.email.send
    required_authority:
        communicate
    expected_effect:
        external communication
```

Then WORK decides execution: whether the work item is created, which authority_scope it carries, which bindings apply, and whether confirmation is required for this payload.

The brief names **capability ids**, not provider tools. Provider selection is not a conversational choice.

Intent != execution. A brief is not a send.

If Advanced cannot map the objective to any briefable capability, it must **not**
emit an empty or otherwise invalid `TaskBrief`. It returns a typed
`UnsupportedBrief` (`status: unsupported`) with a human-readable reason and, when
available, a `closest_capability` drawn only from the product catalogue — never a
fabricated id. Companion presents this as non-executable (“Atlas can't turn this
into Work yet”); it must not invent a capability to make the objective look
briefable.

### Catalogue gap (follow-up)

`coding.software_engineering` exists in the product catalogue as bounded software
implementation work, but it is not currently exposed for
`ADVANCED_CONVERSATION` (`brief`). Objectives such as UI/product design therefore
correctly land as `UnsupportedBrief` today. Whether that capability should become
briefable — or whether product/UI design needs a distinct capability — is an
open catalogue decision, not a reason to weaken `TaskBrief` invariants.

---

## Runtime frame

The runtime frame is the only place actual tools appear.

Example:

```text
Capability:
    communication.email.send

may resolve to:
    gmail.send
    smtp.send
    n8n.workflow.execute
```

The model must not select providers directly. The frame is built from:

1. the Task Brief’s capability list (`catalog()` ids)
2. `CapabilityExecutionProfile` for this deployment, including any `CapabilityBinding`
3. `allowed_tools` as a **frame** allow-list of ToolDescriptor refs from that profile
4. ToolGateway as the invoke boundary

The frame is narrower than provider inventory. `list_credentials` and `archive_workflow` stay out unless a reviewed capability binds them into **this** work.

Capability awareness != tool availability. A capability can be known in CHAT (`explain`) and still have no tools in that interaction.

---

## Invariants

```text
Capability awareness != tool availability

Exposure != authority

Authority != confirmation

Task intent != execution

Provider discovery != capability exposure
```

Also:

- Discovery != capability
- Capability != implementation
- Capability != permission
- Binding != permission
- Tool != capability
- Intent != execution
- Execution != verification
- A conversational “yes” is not confirmation
- WORK `execute` exposure still requires standing grant and, where declared, payload confirmation

---

## Future control plane

Conceptual only. No UI in this change.

The future UI manages Atlas concepts, not vendor toggles:

```text
Capabilities
    |
    +-- available
    +-- confirmation requirement

Exposure policies
    |
    +-- CHAT
    +-- ADVANCED_CONVERSATION
    +-- WORK

Bindings
    |
    +-- provider implementations
```

Not:

```text
n8n tool permissions
MCP endpoints
raw API calls
Enable n8n tool X
Disable n8n tool Y
```

Prefer:

```text
Enable workflow execution capability
CHAT: explain
ADVANCED: brief
WORK: execute
Bind implementation to n8n
Require confirmation
```

Unmapped MCP tools never appear on this plane.

---

## What this is not

- Not runtime enforcement
- Not a change to ToolGateway, MCP, or `CapabilityDefinition` identity
- Not RBAC, users, or ACLs
- Not a reason to hide product meaning; hide **handles**, not **awareness**
