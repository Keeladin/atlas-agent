# Mobile Capture V1 — Behavioural Contract

**Status:** Implemented and accepted for V1 offline behaviour  
**Surface:** `atlas_mobile/`  
**Server synchronization:** Not yet implemented

The legacy WhatsApp parser remains the historical/import path. Mobile Capture V1 is how **new** supervisor reports should be born so Atlas does not have to reconstruct information that could have been captured correctly at source.

North star: **minimum supervisor effort with enough structure to preserve operational meaning.**

The current V1 is offline-first and has passed an actual phone-offline acceptance cycle. Authentication and synchronization into the Atlas server runtime remain separate future work.

---

## 1. Unit of capture

One **activity** is one bounded piece of work or observation.

The supervisor records one activity, reaches an acceptable validation state, presses **Next**, and the form resets for the next activity.

Same machine, next job → another activity.

Do not merge activities merely because the machine identity is the same.

---

## 2. Activity categories

V1 distinguishes:

- **Machine** — work or observation associated with a machine/item requiring explicit machine-state semantics;
- **Other operational** — relevant non-machine operational work;
- **Attendance** — attendance information kept separate from machine work.

The UI must not force machine-only fields onto other operational or attendance activities.

---

## 3. Three independent facts for Machine activities

These facts must never be inferred from each other.

| Concept | Records | Must not imply |
|---|---|---|
| **Activity time** | When work occurred, or that this is a no-times standing/status observation | Whether the job finished or the machine is operational |
| **Job outcome** | Whether this activity's work was completed | Machine state |
| **Machine state** | How the machine was left | Whether the work was completed or how long it took |

Examples:

```text
End 14:00 + Completed + Running / operational
```

means the activity finished and the machine was explicitly left operational.

```text
End 14:00 + Completed + Not tested
```

means the work was completed but operation was not confirmed.

```text
End 14:00 + Incomplete / continue + Still under repair / standing
```

means work stopped at 14:00 and remains incomplete.

Clocks never choose outcome or state.

---

## 4. Machine activity fields

A Machine activity captures the minimum required structure:

- machine/item;
- time mode;
- start/end clocks when in timed mode;
- what happened / work performed;
- job outcome for timed work;
- explicit machine state;
- follow-up/unresolved note when required or useful;
- optional free-text detail.

Work/what-happened remains natural text rather than being reduced to a rigid catalogue.

V1 does **not** require fields for breakdown/proactive/statutory classification, downtime classification, parts catalogue, artisan roles or productivity metrics.

---

## 5. Job outcome

For timed Machine activities, job outcome is a closed explicit choice:

- **Completed** — this activity's work was finished. This does not mean tested and does not mean Running.
- **Incomplete / continue** — this activity's work was not finished. This does not choose machine state.

There is no default.

A no-times standing/status observation has no job outcome because it is not a timed work interval.

---

## 6. Machine state

Every Machine activity requires an explicit state before it can be accepted.

Closed V1 states:

- **Running / operational**;
- **Not tested**;
- **Still under repair / standing**;
- **Awaiting parts**;
- **Other** — requires a short explanatory note and explicit confirmation.

Rules:

- Never default to Running.
- Never derive machine state from job outcome.
- Never derive job outcome from machine state.
- Completed ≠ Running.
- Not tested remains a distinct truth state.

If the machine is not operationally closed — or the job outcome is Incomplete / continue — Atlas asks for a short follow-up/unresolved note. Missing follow-up can be an orange condition rather than destroying the activity.

Other operational and Attendance activities do not use machine state.

---

## 7. Times

SOS and EOS are legacy WhatsApp conventions and are **not** Mobile Capture V1 options.

V1 time modes are:

1. **Clocks** — explicit Start time and End time.
2. **No times — standing / status observation.**

There is no Still-busy clock mode, SOS or EOS in Mobile Capture V1.

If a job is unfinished when work stops, record the actual end clock, choose **Incomplete / continue**, and choose the appropriate machine state.

### No-times observations

No-times is valid when the supervisor is recording a standing/status condition rather than a timed piece of work.

Rules:

- The supervisor explicitly chooses no-times.
- Atlas does not infer no-times from blank clocks.
- Work/observation text is required.
- Machine state is required.
- Job outcome is not used.
- A valid no-times observation may be Green.

If clocks mode is selected and either clock is missing or malformed, validation is Red.

---

## 8. Validation model

Validation is deterministic and happens before **Next**.

### Green — valid

The activity is structurally valid and internally consistent.

Next is enabled.

### Orange — requires human confirmation

Orange is for unusual, ambiguous or suspicious but potentially legitimate input.

Examples include:

- an unusual cross-midnight time sequence;
- an `Other` machine state requiring explanation;
- missing recommended follow-up on unresolved work;
- a value that may be a typo but could be real.

Atlas must not silently correct orange input. The supervisor either edits it or explicitly confirms it.

### Red — invalid

Red is used when Atlas cannot safely represent the activity as structured data.

Examples include:

- malformed clock;
- clocks mode with missing start/end;
- required machine/item missing;
- required work text missing;
- required machine state missing;
- impossible field combination.

Next remains unavailable until corrected.

The validation goal is to resolve ambiguity **at capture time**, while the person who knows what happened is still present.

---

## 9. Continuation and unresolved work

Mobile Capture is structured so Atlas can eventually connect work across shifts without inferring continuity from free-form prose.

When synchronized operational state is available in a future server integration, the device may show an unresolved prior thread for the selected machine and offer an explicit choice such as:

```text
Continue previous job
New issue
```

That future feature must remain explicit. Atlas must not silently decide that two activities belong to one operational thread merely because the machine identity matches.

The current offline V1 does not depend on server-side unresolved-state lookup.

---

## 10. Local report record

The current PWA stores reports locally in IndexedDB.

A report contains durable client-side identity and structured information including:

- report type;
- report ID;
- user identity/display/role configuration;
- operational day;
- shift;
- report status;
- activities;
- timestamps.

Activities are persisted locally as the supervisor works rather than waiting until final submission.

Completed reports survive reopen/refresh according to the accepted offline behaviour.

---

## 11. End Report

When all activities are entered, the supervisor chooses **End Report**.

Atlas assembles the structured activities into a reviewable shift report while preserving separate activities.

The report can:

- group presentation by machine/item where useful;
- order activities chronologically;
- preserve multiple jobs for the same machine;
- calculate valid explicit clock intervals;
- identify the last explicitly reported machine state;
- surface unresolved work;
- keep Other operational and Attendance information distinct;
- generate WhatsApp-ready plain text.

The supervisor reviews the complete report before using the external output.

---

## 12. WhatsApp relationship

WhatsApp remains the current official external reporting channel.

The implemented first-stage flow is:

```text
Atlas Mobile Capture
  → End Report
  → Review
  → Generate WhatsApp-ready text
  → Copy
  → Supervisor pastes into official WhatsApp group
```

No automatic WhatsApp sending is required or implied.

Atlas owns the structured source record for reports created in Atlas; WhatsApp remains the external publication channel.

---

## 13. Offline-first requirement

Underground connectivity cannot be assumed.

The following must work without a server connection:

- application shell reopen after installation/caching;
- activity capture;
- deterministic validation;
- draft/report persistence;
- Next flow;
- End Report assembly;
- review;
- WhatsApp text rendering and copy.

The accepted device test proves the current V1 can reopen and operate after the development-host path is removed and the phone is offline.

See `atlas_mobile/PHONE-OFFLINE-ACCEPTANCE.md`.

---

## 14. Server synchronization boundary

Authenticated synchronization is **not yet implemented**.

When added, it must preserve offline-first behaviour rather than make the PWA server-dependent.

Required sync properties include:

- stable report identity;
- idempotent retries;
- no duplicate report if acknowledgement is lost;
- explicit local/syncing/synced/error state;
- schema validation at the server boundary;
- authenticated user/device authority;
- durable server receipt;
- no silent modification of a completed local report.

Intended future path:

```text
Atlas Mobile PWA
  ↓ when coverage returns
Authenticated HTTPS sync
  ↓
mobile.report.ingest
  ↓
TaskRuntime
  ↓
artifact / evidence / operational state
```

Sync technology remains an edge concern; it must not distort the capture contract.

---

## 15. Legacy parser relationship

The existing WhatsApp parser remains valuable for:

- historical data;
- imported reports;
- transition/fallback;
- reports created outside Atlas.

It remains a conservative best-effort interpreter.

The goal is not to make the legacy parser infer every possible reporting style perfectly. New reports should capture important structure at source.

---

## 16. Product constraint

Mobile Capture must not become a large management form.

The intended experience is:

```text
record what happened
  → resolve anything unclear
  → Next
```

not:

```text
finish shift
  → complete another long administrative form
```

Fields or steps should only be added when a demonstrated operational responsibility justifies the burden.

---

## 17. Current implementation truth

Implemented in `atlas_mobile/`:

- installable PWA shell;
- service worker;
- activity-at-a-time capture;
- machine/user directory data;
- deterministic validation;
- IndexedDB persistence;
- report records;
- End Report assembly;
- WhatsApp-ready text rendering;
- fixture suite;
- true offline phone acceptance record.

Not yet implemented:

- authenticated Atlas server sync;
- server-side report ingest capability;
- server-to-phone unresolved-thread/bootstrap state;
- device/user auth integration.

The owner/admin Companion PWA is a separate implemented surface (`atlas_companion/`). It must not inherit supervisor reporting authority, and Mobile Capture must not inherit Companion model/credential controls.

These future surfaces must integrate through Atlas 2.0 runtime boundaries rather than turning Mobile Capture into a separate agent or source of server truth.
