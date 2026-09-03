# Atlas Interface Design

## Purpose

This document is the visual and interaction source of truth for Companion. Runtime truth remains owned by Atlas; the interface exposes it without inventing parallel state.

## Core doctrine

Atlas is one living operational surface: dense with runtime truth, visually continuous, and organized by hierarchy rather than containers.

- Information sits on the surface.
- One owner surface is primary; Work, Cadence, attention, conversation, evidence and current execution remain co-visible when relevant.
- Deep configuration is plumbing. Policy, providers, connections, capabilities, filesystem details and raw receipts remain available through Control or inline expansion without becoming the primary navigation model.
- Group by relationship before container. Use position, alignment, typography, hairlines, tone and depth before drawing a box.
- Containers are reserved for real runtime boundaries: confirmation, destructive decisions, faults, isolated media viewports, and similar interventions.
- Motion communicates execution, arrival, provenance and state change. It is never decorative justification for an otherwise empty screen.

## Design dimensions

### 1. Constraints

Primary workstation: a 27-inch desktop display. The design must remain functional at common 2560px, 1920px and 1440px desktop widths and on the installed PWA at mobile widths. The desktop surface should use available real estate rather than simulating mobile scarcity.

### 2. Accessibility

Target WCAG 2.2 AA. Maintain visible keyboard focus, text/icon state equivalents for semantic colour, and 44px touch targets where touch is expected. `prefers-reduced-motion` removes decorative motion and preserves state comprehension.

### 3. Information architecture

The primary owner interface answers continuously:

1. What are we doing?
2. What is true now?
3. Does Atlas need the owner?

Conversation is not a separate product from Operations. Work, Cadence, evidence and capability activity surface around the current objective. Control remains a deeper technical destination.

### 4. State design

Every primary surface must deliberately cover: initial, loading, empty/idle, active execution, partial/uncertain, failure and offline. Confirmation is an owner-attention state attached to the exact action it governs.

### 5. Content experience

Conversation is a continuous editorial transcript, not bubbles or turn cards. Media, documents, evidence, execution traces and controls expand inline beside the objective they belong to. Rich output should use available width rather than becoming a thumbnail inside another panel.

### 6. Visual hierarchy

Priority is: current objective/conversation → active execution/evidence → persistent operational awareness → technical provenance. Scale, placement, contrast and typography establish this hierarchy. Technical IDs stay compact and monospaced.

### 7. Emotional design

Atlas should feel high-resolution, modern, vibrant, precise and deeply capable. It should not look sepia, matte, militaristic, sci-fi, or like generic enterprise SaaS. The canvas is deep neutral blue-black; saturated colour is semantic and luminous rather than decorative.

### 8. Input experience

Enter sends. Shift+Enter creates a newline. Controls live beside the information they affect. Ordinary inspection expands in place rather than routing through nested pages or modal chains.

### 9. Microcopy

Prefer concise runtime language: `Verified`, `Working`, `Waiting`, `Needs you`, `Blocked`, `Evidence`. Copy must map to real runtime semantics rather than inventing friendly but inaccurate state.

### 10. Motion

Required motion communicates feedback or state transition; optional motion adds atmosphere only when it does not obscure truth. Reduced-motion mode disables optional motion and retains direct state changes.

### 11. Tokens

Structural neutrals and semantic state colours are separate token families. Current signals are blue/cyan for active information and connectivity, violet for model/retrieval context, emerald for verified success, amber for attention/waiting, and red for failure/blocking. Legacy gold variable names may exist temporarily as compatibility aliases but must not define visual meaning.

## Typography

Use a modern system-first sans stack with strong editorial hierarchy and a dedicated monospaced stack for IDs, hashes, timestamps and execution evidence. Do not default to Inter or a serif heading style merely because they are convenient. The current implementation prefers Aptos/Segoe UI Variable/SF Pro/Helvetica Neue fallbacks.

## Validation

A successful primary surface passes these tests:

- Five-second glance reveals current objective, Atlas activity and owner-attention state.
- Common work does not require navigating three levels deep.
- Removing borders does not destroy hierarchy.
- Colour is never the only carrier of state.
- A 27-inch display contains useful operational truth rather than decorative empty space.
- Mobile reflows the same truths instead of silently deleting them.
- UI state can be traced to runtime state or evidence.
