# Mobile Capture V1 — Behavioural Contract

Status: **proposed for review. Not authorised to implement.**

The WhatsApp parser remains the frozen legacy/import path. This contract is only how **new** supervisor reports should be born so Atlas does not reconstruct information that could have been captured at source.

North star: minimum supervisor effort; enough structure that morning reconstruction is no longer required for reports created in Atlas.

No UI framework, database, sync stack, authentication, or model is chosen here.

---

## 1. Unit of capture

One **activity** is one bounded piece of work or observation.

The supervisor records one activity, reaches **Green**, presses **Next**, and the form resets. They repeat until **End Report**.

Same machine, next job → another activity. Do not merge on identity.

---

## 2. Three independent facts (Machine)

These must not be inferred from each other:

| Concept | What it records | What it must not imply |
|---|---|---|
| **1. Activity time** | When work was done, or that there was no work interval | Whether the job finished, or whether the machine is operational |
| **2. Job outcome** | Whether *this* work was finished | Machine state. Incomplete does not pick a machine state. Completed does not mean Running. |
| **3. Machine state** | How the machine was left | How long they worked, or whether the job is closed |

Example:

- End 14:00 + **Completed** + **Running / operational** → repaired at 14:00 and confirmed operational.
- End 14:00 + **Incomplete / continue** + **Still under repair / standing** → worked until 14:00; repair not finished.

A time near the end of shift **never** implies success or failure. 14:00 is only a clock.

---

## 3. Activity kinds

Exactly three:

| Kind | Use |
|---|---|
| **Machine** | Work or status on a named equipment identity |
| **Other operational** | Farm gates, empty parts/scotch car, stop-and-fix, standing-at-location notes, logistics |
| **Attendance** | Who is absent / all at work |

This exists so “Absent”, farm gates, and empty cars never receive machine-state semantics.

---

## 4. Fields

| Field | Machine | Other operational | Attendance |
|---|---|---|---|
| Subject | Required (pick known identity or type a new one) | Required (short label) | Not a machine. Names if anyone is absent |
| Work / what happened | **Required** free text | **Required** free text | Names or “all at work” |
| Time mode | Required: **clocks** or **no-times** | Required (often no-times) | Not required |
| Start time | Required in clocks mode | Per mode | — |
| End time | Required in clocks mode | Per mode | — |
| **Job outcome** | **Required** on timed Machine activities. Not used on no-times standing/status. **No default.** | Optional / N/A | Not used |
| **Machine state** | **Required** on every Machine activity, including no-times. **No default.** | Optional / N/A | Not used |
| Unresolved / follow-up | Optional unless an open condition (below) | Optional | — |
| Continue vs new issue | Required only if an unresolved prior for that subject is visible on the device | Same if applicable | — |
| People | Optional free text | Optional | — |
| Extra detail | Optional free text | Optional | — |
| Media | Out of this contract | Out of this contract | — |

Work / what happened is never a dropdown.

**Not in this contract:** breakdown vs proactive vs statutory, downtime vs labour interval, required location, parts catalogue, artisan roles, productivity, **SOS/EOS as capture options**.

---

## 5. Job outcome (timed Machine activities)

Closed list. No default. Not inferred from clocks or from machine state.

| Outcome | Meaning |
|---|---|
| **Completed** | This activity’s work was finished. Does **not** mean tested. Does **not** mean Running. |
| **Incomplete / continue** | This activity’s work was not finished. Does **not** choose the machine state. |

Incomplete at 14:00 is still Incomplete. The clock does not upgrade it to Completed because shift-end is near.

No-times standing/status observations have **no job outcome**. There is no work interval to complete.

---

## 6. Machine state (every Machine activity — required)

Every Machine activity requires an **explicit** machine state before Next, including standing/status observations.

This is intentional. A recurring failure in the WhatsApp corpus is that the supervisor describes the work and does not say how the machine was left. Capture must close that gap.

**Do not default machine state to Running.**  
**Do not default job outcome.**  
**Do not set one field from the other.**

### Closed list

| State | Meaning |
|---|---|
| **Running / operational** | Confirmed operational. Only this value means the machine was left running / confirmed working. |
| **Not tested** | Not tested (e.g. no operator). Compatible with Completed or Incomplete. |
| **Still under repair / standing** | Not in finished service: repair ongoing, parked, workshop, or otherwise standing. |
| **Awaiting parts** | Waiting on material. |
| **Other** | Only if none of the above fit. Requires a short free-text note. Orange until they confirm Other is necessary. |

**Completed ≠ Running.** Hose replaced, not tested → Job outcome Completed + Machine state Not tested (or Still under repair / standing). Never auto-Running.

If machine state is Not tested, Still under repair / standing, Awaiting parts, or Other — or job outcome is Incomplete / continue — Atlas **asks** for a one-line follow-up. Skipping that note is orange, not red.

Other operational: no required machine state or job outcome.  
Attendance: neither field is used.

---

## 7. Times (mobile capture)

SOS and EOS are **legacy WhatsApp conventions**. They are **not** Mobile Capture V1 options. The frozen importer still accepts them on historical/external reports.

### Modes

1. **Clocks** — explicit **Start time** and **End time**.  
2. **No times — standing / status observation.**

There is no Still-busy clock, no SOS, no EOS.

If work is still active when the report is compiled, say so with **Job outcome = Incomplete / continue** and the appropriate **machine state**. Do not omit the end clock or type EOS. If they worked 18:00–02:00 and the job is unfinished, end time is 02:00 (or whenever they stopped), outcome Incomplete, state Still under repair / standing (or Awaiting parts, etc.).

### No-times standing / status observations

A Machine activity with **no clocks is legitimate** when the supervisor is recording a condition, not a timed job.

Corpus: L91 standing at 763 with two flat tyres; L105 “Needs tyre”; ARB4 at 833, compressor keyway missing; standing 813S where DT04 is parked.

Rules:

- They must **choose** no-times. Atlas does not infer “forgot times” from blank clocks.
- Work/observation text and **machine state** are still required. Job outcome is not used.
- Chosen no-times + text + machine state is **Green**. It is not orange merely because clocks are absent.

If they choose clocks mode and leave start or end empty → Red. That is not a silent fall-through to no-times.

### Duration

Calculate a **reported work/activity interval** only when both start and end are numeric clocks and the pair is not orange-suspicious. Never call it downtime.

### Suspicious clocks — structural, not a duration cutoff

Do **not** use a fixed duration threshold (including 8 hours).

| Pattern | Treatment |
|---|---|
| End ≥ start (e.g. 09:00–13:30, 20:40–21:45) | Green. Calculate interval. |
| Start in the evening (~18:00–23:59) and end in the early morning (~00:00–05:59) (e.g. 22:30–01:55, 21:00–00:00) | Ordinary overnight span. Green. Calculate interval. |
| End earlier than start, and it is **not** that evening→early-morning pattern (e.g. `22h00–20h45`; `10:30–00:10`; `15:00–09:00`) | Orange. Keep written clocks. **Do not rewrite.** Supervisor confirms or edits. Duration withheld until Green. |
| Unreadable clock; start or end missing in clocks mode | Red. |

Evening/early-morning windows only classify a wrap. They are not shift start/end substitutes.

---

## 8. Multiple activities on one machine

**115 of 168** replay days have two or more rows for the same item.

Next always starts a **new** activity. STC12 pump `21h00–00h00` and brakes `01h14–03h00` are two activities. SST21 hose and SST21 sensor the same night are two activities unless Continue is chosen.

End Report may group by subject for reading. Stored activities stay separate.

---

## 9. Continuation

Ask **Continue previous / New issue** only when the device already has an unresolved activity for that subject (this draft, or a last cached completed report).

Unresolved means prior **Incomplete / continue**, or prior machine state Not tested / Still under repair / standing / Awaiting parts / Other, unless a later Continue activity has already closed it with Running / operational.

Example: `ARB4 — still under repair: fitting broke / no fitting UG` (2026-04-09).

- **Continue:** this activity is the next step. Later Running / operational **closes** the earlier open thread. That is progression, not a conflict.
- **New issue:** independent. Same machine is not evidence of continuity (SST21 ×2, 2026-05-05).

If nothing unresolved is visible offline, do not ask and do not invent Continue.

Job outcome Incomplete / continue on *this* activity is not the same question as Continue previous. Incomplete describes this interval. Continue links to an earlier open job.

---

## 10. Green / orange / red

Deterministic. No model.

### Red — Next disabled

- Machine or Other: no subject, or empty work text.  
- Machine: no machine state chosen.  
- Timed Machine: no job outcome chosen.  
- Clocks mode: start or end missing, or malformed clock.  
- Unresolved prior visible and Continue/New not chosen.

### Orange — Next disabled until edit or explicit confirm

- Suspicious clock pattern in §7.  
- Incomplete, or machine state Not tested / Still under repair / standing / Awaiting parts / Other, with no follow-up note.  
- Machine state Other (confirm it is necessary).  
- Newly typed machine identity (confirm spelling once).

No-times, fully filled (text + machine state), is **not** orange.

Atlas never silently corrects clocks, job outcome, or machine state.

### Green — Next enabled

Required fields present; machine state explicitly chosen; job outcome explicitly chosen when required; no red; every orange acknowledged.

---

## 11. What Next requires

Green.

Then persist the activity in the **local draft** immediately, reset the form, stay on the same report. Next is not submit, sync, or WhatsApp.

---

## 12. What End Report assembles

Local, offline, this report only:

- Who / operational day / day or night (once at report start).  
- Activities grouped by subject; each activity keeps its own start/end or no-times, work, job outcome (if any), and machine state.  
- Chronological order within subject.  
- Calculated work intervals only where §7 allows.  
- Last machine state per thread; Continue + later Running closes the earlier open thread.  
- Unresolved list (Incomplete and/or open machine states + notes).  
- Attendance, if any.  
- Other operational activities in their own group.  
- Plain-text WhatsApp rendering, copyable. That text uses the captured clocks or omits times for no-times entries. It does **not** invent SOS/EOS.

Review confirms that assembly. They may edit an activity; they should not reconstruct a blob.

End Report does not require network and does not send WhatsApp. The supervisor copies and pastes into the official group.

---

## 13. Offline

Must work without a server: create draft, add/edit/delete activities, validation, Continue/New against **local + cached** unresolved only, End Report, review, generate and copy WhatsApp text, keep the report on the device.

Completed reports sync when coverage returns, with explicit status (local only / syncing / synced). Connectivity is for sync, not for creation.

Loss of coverage must not drop a Next’d activity, duplicate it, or change a confirmed value.

Sync technology is out of scope.

---

## 14. Report start (once)

Not per activity: supervisor identity, operational day (06:00–05:59), day or night shift.

---

## 15. Acceptance examples (must hold)

| Case | Required behaviour |
|---|---|
| 14:00 end + Completed + Running | Green. Interval calculated. Means repaired at 14:00 and confirmed operational. |
| 14:00 end + Incomplete + Still under repair / standing | Green. Worked until 14:00; job not finished. Time does not imply failure or success. |
| Hose replaced, not tested | Completed + **Not tested**. Never auto-Running. Follow-up asked. |
| L91 standing, two flats, no clocks | No-times + machine state Standing (or Awaiting parts). No job outcome. Green. |
| Work still going at compile time | Clocks for the interval actually worked + Incomplete + appropriate machine state. Not EOS. |
| ARB4 10:30–00:10 | Orange. Clocks unchanged. Duration withheld until confirm or edit. |
| TDR10 22h00–20h45 | Orange (backwards, not evening→morning). Duration withheld. |
| STC12 two intervals | Two activities; each keeps its own work text. |
| SST21 two unrelated jobs | Two activities; Continue not offered unless one is still open. |
| ARB4 Incomplete / still under repair → later Completed + Running | Continue + Running closes the thread. Not a conflict. |
| Farm gates / empty scotch car | Other operational. No required machine state. |
| Named absences | Attendance. No machine state. |
| Next with work text but no machine state | Red. |
| Next with clocks but no job outcome | Red. |
| SOS or EOS offered as a time control | Contract failure. |

---

## 16. Failure of this contract

The contract has failed if:

- Next is possible on a Machine activity without an explicit machine state;  
- Next is possible on a timed Machine activity without an explicit job outcome;  
- Job outcome, machine state, or clocks are inferred from each other;  
- Completed is treated as Running;  
- Either field defaults;  
- A time near shift-end is treated as Completed or Running;  
- SOS or EOS is a capture option;  
- Incomplete work is represented by omitting the end clock;  
- A standing/status observation cannot be saved without fake clocks;  
- Suspicion is a magic duration number rather than the clock-pattern rules in §7;  
- Supervisors must fill work-type, downtime, location, or parts to leave the screen;  
- Capture requires a network.

---

## 17. Relationship to the legacy parser

Unchanged. Historical and external WhatsApp reports still enter through the frozen V1 importer, which may still see SOS/EOS in free-form text.

This contract is only for reports created in Atlas. Mobile Capture V1 does not write SOS/EOS.

---

Proposed for review. Not authorised to implement.
