# Atlas Morning Workflow — Behavioural Specification v1

**Status:** Implemented and frozen for V1 behaviour  
**Runtime capability:** `operations.morning_pack.generate`  
**Role:** Deterministic domain responsibility executed through Atlas 2.0 TaskRuntime

This document defines the current behavioural contract for the TMM Morning Workflow.

The workflow predates the general Atlas 2.0 runtime, but it is now integrated as a deterministic composite capability. The runtime owns the task, execution record, artifact, evidence and completion state; the Morning Workflow continues to own its domain parsing and rendering rules.

The Atlas 2.0 integration must **not** loosen the conservative V1 rules below.

---

## 1. Purpose and success

Atlas consumes already-written supervisor shift reports and produces the working morning table used for the TMM engineering departmental meeting.

V1 succeeds if the table and highlighted exceptions materially reduce the need to reconstruct the operational picture manually from the shared reporting source.

V1 fails if Atlas:

- invents status, times, people or relationships;
- silently merges separate work;
- drops unusual but legitimate entries because they do not match a preferred format;
- converts a reported state into a claim of current live machine state;
- requires the user to reread every original report to trust ordinary rows.

Exception review is the intended human interaction. The user should not have to approve every row.

---

## 2. Domain roles

These are human operational roles, not software identities or agent personas.

| Person | Role in the workflow |
|---|---|
| **Jaco Fouché** | TMM engineering lead and consumer of the pack. His own reporting stream is not part of frozen V1 supervisor rows. |
| **Lyle** | Current shift supervisor and V1 report author. |
| **Jurie Venter** | Current shift supervisor and V1 report author. |
| **Fanie Lombard** | Historical shift supervisor; legitimate author only inside configured historical tenure. |

Other names appearing inside report bodies are people involved in the work, not report authors.

An unclear sender must be flagged or ignored according to the source-selection rules; Atlas must never guess that an unknown label is one of the configured supervisors.

---

## 3. Input contract

The workflow accepts messages from a replaceable source adapter. WhatsApp export text is the implemented source format, but WhatsApp is not the permanent product ontology.

Each source message provides at least:

- sender identity as supplied by the source;
- source timestamp;
- free-form text, including multiline content;
- media references or omission markers where present.

Raw source text is preserved.

### Configuration

Persistent configuration defines:

- relevant senders and optional tenure bounds;
- sender aliases;
- item aliases;
- operational-day boundary;
- approximate shift windows as contextual hints only;
- usual post times as locating aids only.

Aliases are data, not code.

### Out of frozen V1 report-source scope

The Morning Workflow does not treat the following as V1 supervisor report sources:

- Jaco Fouché's own day/statutory/planned stream;
- other departments' messages;
- control-room reports;
- separate safety or attendance systems.

Those may become additional Atlas capabilities or input streams later without changing this frozen parser contract.

---

## 4. Operational day

The operational day starts at **06:00** and ends at **05:59** the following calendar day.

Example:

```text
Operational day 2026-03-25
= 2026-03-25 06:00 through 2026-03-26 05:59
```

The morning meeting time is consumption context, not a reporting-cycle boundary and not a duration clock.

### Assignment order

Assign a reporting unit to the operational day/shift it describes using:

1. explicit report text and heading;
2. work clocks as written;
3. submission time as a secondary locating aid.

Do not assign a report solely because a timestamp crossed 06:00.

A late night report that clearly describes the shift that just ended remains attached to the operational day that ended at 05:59 and is flagged late.

If a day or night report is missing, flag the missing reporting unit. Do not invent it.

If day/shift association is uncertain, retain the candidate conservatively and surface the uncertainty rather than guessing.

---

## 5. Sender and reporting-unit filtering

Filtering happens before body interpretation.

1. Keep configured relevant senders active at the source timestamp.
2. Exclude unrelated senders even when they mention familiar machines.
3. Preserve excluded raw messages in the loaded source where applicable; exclusion from rows is not deletion.
4. Never infer a supervisor identity from a similar-looking sender label.

A reporting unit is kept when it has operational report shape such as:

- machine/item-like content;
- time ranges;
- attendance/absence content;
- explicit report headings;
- continuation blocks associated with a supervisor report.

When uncertain, prefer preserving source material and flagging it over silently discarding potentially relevant work.

There is no hardcoded TMM machine-prefix allowlist.

---

## 6. Entry extraction

A reporting unit is converted into separate entries. Separate jobs remain separate rows.

Each entry can contain:

- `item`;
- raw reported period;
- parsed `start` / `end` when explicitly numeric;
- start/end kind;
- what happened;
- work/finding;
- last reported state;
- follow-up/unresolved work;
- names as written;
- work character only when supported by source wording;
- media-present marker;
- source reference;
- verbatim exceptions.

### Non-negotiable extraction rules

- Missing information stays missing.
- No clock may be invented.
- No state may be invented.
- `Running` or `complete` are represented as **reported** source states, never as unqualified live truth.
- A machine that was not tested must not be described as operational merely because work was completed.
- Unusual item labels are retained rather than dropped.
- Separate source blocks are not silently merged.

Deterministic extraction is preferred. Ambiguous prose may use bounded interpretation where explicitly configured, but deterministic postconditions remain authoritative. If interpretation is unavailable, preserve the source conservatively instead of dropping it.

---

## 7. Times and intervals

Reported activity interval is calculated only when both start and end are explicit numeric clocks.

```text
start + end numeric
  → deterministic reported_work_interval
```

Cross-midnight arithmetic is ordinary clock arithmetic.

Legacy source markers such as SOS, EOS, open, still-busy or missing do **not** receive invented numeric durations.

A calculated activity interval is not automatically machine downtime. Downtime may only be stated when the source explicitly supports that interpretation.

Overlapping intervals for the same normalized item are surfaced as an overlap; they are not blindly added into a machine downtime total.

---

## 8. Conservative reconciliation

The workflow does not merge rows merely because two entries mention the same machine.

Item aliases may group rows under a normalized identity for presentation while preserving each source entry.

Supported exception concepts include:

- possible continuity;
- conflict between reported states;
- incomplete times;
- state not established;
- not tested;
- unresolved work;
- awaiting parts;
- media not interpreted;
- uncertain item;
- uncertain day/shift;
- late report;
- non-trivial interpretation;
- overlapping numeric intervals.

Explicit continuation written inside one source activity remains one activity because the source says so, not because a merger inferred it.

When two rows report different states, both remain visible. The workflow does not pick a winner without evidence.

---

## 9. Output contract

The working pack contains a Markdown table with these required columns:

```text
Machine / Item
Reported period
What happened
Work / finding
Last reported state
Follow-up / unresolved
```

The pack also preserves or exposes:

- operational-day identity;
- reporting units/senders used;
- exception flags;
- raw source references;
- media markers.

The Atlas 2.0 capability verifier checks that the generated pack is non-empty, has the expected operational-day identity and contains the required structural columns.

---

## 10. Corrections and aliases

Two classes of correction remain distinct.

### Persistent alias knowledge

Known item or sender aliases may persist in configuration and apply to later packs.

Example:

```text
STC9 = STC09
```

Normalization does not merge separate jobs.

### Pack-local correction

A correction that applies only to one generated pack remains scoped to that operational day/output and must not silently become a global rule.

Examples:

- correcting a displayed state label for that pack;
- dismissing a possible-continuity flag for that pack.

Raw messages remain unchanged.

---

## 11. Evidence and truth boundary

The Morning Workflow produces a deterministic `morning_pack` artifact through Atlas 2.0.

Its capability execution records:

- task and execution identity;
- source request artifact;
- operational day;
- output artifact;
- receipt;
- calculated claim(s);
- verification outcome.

The Morning Workflow's source-derived language remains intentionally conservative. A `last reported state` is evidence about what the supervisor reported, not a guarantee of the machine's present live condition.

---

## 12. Current runtime integration

The implemented path is:

```text
CLI / future API
  ↓
TaskRuntime
  ↓
operations.morning_pack.generate
  ↓
existing atlas_morning deterministic code
  ↓
morning_pack artifact + receipt + claim
  ↓
Morning output verifier
  ↓
Task completion gate
```

Atlas 2.0 wraps the domain workflow; it does not replace its conservative behaviour with model improvisation.

---

## 13. Validation requirements

Regression coverage must preserve at minimum:

- sender and tenure filtering;
- operational-day assignment;
- late report behaviour;
- missing shift detection;
- no prefix-based dropping;
- numeric interval arithmetic;
- no SOS/EOS duration invention;
- not-tested semantics;
- media markers;
- user stream exclusion from V1 rows;
- no silent row merging;
- overlap detection without bogus totals;
- conflict visibility;
- persistent aliases;
- pack-local corrections;
- ordinary valid rows remaining unflagged;
- runtime capability output contract.

The repository's Python regression suite is the executable source of truth for detailed edge cases.

---

## 14. Change rule

This V1 behaviour is frozen because it encodes tested operational assumptions.

New input streams, richer operational state, supervisor Mobile Capture, control-room ingestion or management dashboards should be added through new capability boundaries rather than silently changing the meaning of this historical/import workflow.
