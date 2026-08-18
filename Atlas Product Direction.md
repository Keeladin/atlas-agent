# Atlas Product Direction

**Status:** Current direction  
**Scope:** Near-term product evolution beyond the implemented core

This document records where Atlas should grow next without pretending that planned interfaces or deployment components already exist.

The current implementation truth is summarized in `README.md`. Where this document describes future work, it is explicitly marked as such.

## 1. Mobile is a first-class interface

Atlas must not assume a desktop-only user.

The implemented supervisor Mobile Capture surface already proves the basic direction:

- activity-at-a-time capture;
- deterministic green/orange/red validation;
- local IndexedDB persistence;
- service-worker offline shell;
- End Report assembly;
- WhatsApp-ready plain text generation;
- true offline phone acceptance.

The current mobile implementation does **not** yet synchronize reports into the Atlas server runtime.

## 2. Supervisor reporting: Atlas captures, WhatsApp still publishes

The intended operational workflow remains:

1. Supervisor records the shift report in Atlas Mobile.
2. Atlas stores the structured report locally on the device while offline.
3. Atlas assembles the completed report and generates WhatsApp-ready text.
4. Supervisor reviews it and taps **Copy**.
5. Supervisor pastes the text into the existing official engineering WhatsApp group.
6. When secure server connectivity exists, the structured report synchronizes into Atlas.

WhatsApp remains an external reporting channel. Atlas becomes the controlled structured source for reports created in Atlas.

Automatic WhatsApp sending is not part of the current product direction.

## 3. Mobile synchronization is the next missing bridge

The offline capture surface is implemented; the server bridge is not.

The intended sync topology is:

```text
Supervisor phone
  ↓
Atlas Mobile PWA
  ↓ when connectivity returns
Authenticated HTTPS sync
  ↓
Atlas Mobile API
  ↓
authentication + schema validation + idempotency
  ↓
mobile.report.ingest capability
  ↓
TaskRuntime
  ↓
artifacts / evidence / operational state
```

Sync must be safe under weak or intermittent connectivity.

Required properties:

- a completed report has a stable client-generated identity;
- retries are idempotent;
- a lost acknowledgement does not create a duplicate report;
- the phone exposes clear `local`, `syncing`, `synced` and `error` state;
- server acceptance returns a durable receipt;
- authentication and authorization are explicit;
- no network failure may silently alter or discard a locally completed report.

## 4. Server-to-phone operational bootstrap

Once server sync exists, Atlas should also be able to send a small bounded operational snapshot to the supervisor device.

Useful synchronized reference state can include:

- machine directory;
- enabled/disabled machines;
- user/supervisor identity configuration;
- unresolved machine threads;
- last known reported states;
- validation/config version.

This state should be cacheable so the supervisor can still work underground with no connection.

The offline app must remain useful when the server cannot be reached.

## 5. Atlas Companion PWA

A separate personal **Atlas Companion PWA** is planned for the owner/admin interface.

It should use the same Atlas backend and durable task truth while exposing a different authority surface from supervisor reporting.

Expected areas include:

- conversational requests;
- task status and history;
- approvals;
- server and provider health;
- operational summaries;
- artifacts and results;
- notifications;
- settings and authority controls.

Conceptually:

```text
Phone / browser
   ↓ HTTPS
Atlas Companion PWA
   ↓
Atlas API
   ↓
TaskRuntime
   ↓
capabilities / tools / model router
```

The browser must not communicate directly with model-provider endpoints.

## 6. One backend, different authority surfaces

The Companion and Supervisor PWA are interfaces to the same Atlas system, not separate agents.

```text
Atlas Server
│
├── Companion PWA
│   └── owner/admin authority
│
├── Supervisor Mobile PWA
│   └── bounded reporting authority
│
└── Atlas API / TaskRuntime
    └── durable shared system truth
```

A supervisor reporting interface must not inherit owner/admin controls merely because both applications use the same backend.

## 7. Always-on deployment

Atlas is intended to move from a development-workstation runtime to an always-on host.

The canonical runtime must remain deployment-agnostic, but the near-term target is:

```text
Developer workstation
  └── Git / VS Code / SSH / browser
             ↓
      Always-on Atlas host
      ├── Atlas runtime / API
      ├── durable SQLite + artifacts + knowledge
      ├── local model service
      └── optional GPU acceleration
```

The development workstation should be able to be turned off without taking Atlas operational state offline.

Production packaging, reverse proxy/tunnel choice, authentication and service supervision are deployment work and are not yet claimed as implemented on `main`.

## 8. Knowledge and retrieval direction

SQLite/FTS is the current implemented retrieval plane.

Semantic/vector retrieval is a future option, not an assumed requirement. It should be introduced only if retrieval evaluations demonstrate a real failure mode that keyword/full-text retrieval cannot solve adequately.

If introduced, it must preserve the same capability, evidence and provenance boundaries rather than becoming a parallel memory architecture.

## 9. Resource management direction

Because the intended Atlas host may share CPU, RAM, GPU and storage with other workloads, host-resource awareness is a useful future capability.

That capability should be deterministic and policy-governed rather than delegated to free-form model judgement.

Possible future responsibilities include:

- inspect CPU, RAM, disk, temperature and GPU/VRAM state;
- inspect approved service/container resource use;
- decide whether an expensive model/job can start safely;
- pause or defer explicitly deferrable Atlas work;
- start/stop only workloads for which authority has been granted;
- preserve critical Atlas services and reporting availability.

This is **direction only** and is not implemented in the current runtime.

## 10. Product guardrails

Future interface and deployment work must preserve the current architecture:

- one Atlas identity;
- durable tasks own substantive work;
- interfaces do not become stores of truth;
- models remain capability providers;
- ContextBuilder owns model context assembly;
- evidence and receipts remain durable;
- authority is explicit and per action;
- deterministic work remains deterministic;
- mobile remains usable offline where the operational environment requires it;
- no new infrastructure is added merely because it is fashionable.

## Near-term sequence

The next practical product sequence is:

```text
always-on host
  → stable Atlas API surface
  → authentication / authorization
  → Companion PWA
  → Mobile Capture server sync
  → richer operational state and notifications
```

The order may change when a real operational responsibility creates a stronger reason, but runtime truth must remain independent of interface implementation.
