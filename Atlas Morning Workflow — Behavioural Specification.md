# Atlas Morning Workflow — Behavioural Specification v1

Status: **READY TO SPEC**  
Scope: first useful version only.  
This document specifies behaviour. It does not specify a database, framework, model, service topology, agent design, or WhatsApp integration mechanism.

---

## 0. Purpose and success

Atlas consumes the already-written supervisor shift reports and produces the working morning table for the TMM engineering departmental meeting (~05:30).

V1 succeeds if reviewing that table, plus highlighted exceptions, takes materially less time than reconstructing the picture from the shared group by hand — and if ordinary (unflagged) rows can be trusted without reading every original line.

V1 fails if the user must still read every original report to trust the output, if correction costs as much as manual compilation, if Atlas invents status/times/merges, or if supervisors must change how they report.

Exception review is the intended human interaction. The user does not approve every row.

### 0.1 Domain roles (not software identities)

These are human operational roles. Atlas processes their reports. It does not treat them as agents, tools, or personalities.

| Person | Role in this workflow |
|---|---|
| **Jaco Fouché** | TMM engineering lead; consumer of the morning pack. Occasional author of his own day-shift team posts. Those posts are a **later input stream**, not V1. |
| **Lyle** | Shift supervisor reporting to Jaco. V1 current author. Alternates weekly day/night with Jurie. Team of artisans and engineering assistants rotates with him. Work: breakdowns, fault finding, repairs, recovery, immediate operational support. |
| **Jurie Venter** | Shift supervisor reporting to Jaco. V1 current author. Same rotation and team pattern as Lyle. |
| **Fanie Lombard** | Former shift supervisor, replaced by Jurie. Legitimate V1 author **only** during his configured supervisor tenure (historical replay). Not an unrelated poster in that period. Not a current supervisor after replacement. |

Other people named **in report bodies** (artisans, engineering assistants, “ask Machiel to repair”) are people mentioned in the work, not report authors and not Atlas roles.

Other WhatsApp senders in the shared engineering supervisory group are other departments or commentators. They must not become V1 authors because they mention a familiar machine.

If a sender or role is unclear, **flag it**. Do not guess that an unknown label is Lyle, Jurie, Fanie, or Jaco.

---

## 1. Accepted inputs

### 1.1 Runtime input (replaceable source)

Atlas accepts a set of messages from a replaceable adapter. WhatsApp is one source, not the product. V1 development may use an exported chat or otherwise supplied messages.

Each message provides at least:

- sender identity as given by the source;
- source timestamp (when the message was submitted);
- free-form text (possibly multiline);
- any media references the source still has (filename, omitted-media marker, or explicit “photos posted …” wording).

Atlas records these as given. It does not require live WhatsApp, control-room files, Teams workbooks, or any other stream.

### 1.2 Configuration input

These are persistent settings, not per-morning data entry:

- **Relevant senders**, time-bounded if needed. V1 current: Lyle; Jurie. V1 historical replay: Fanie during the period he was the supervisor; Jurie thereafter; Lyle throughout. Overlap days include every sender configured as relevant at that timestamp.
- **Operational-day boundary:** 06:00 through 05:59 the following morning. This is the reporting-cycle boundary.
- **Approximate shift windows** (context only): day shift ~06:00–14:00; night shift ~18:00–02:00. These windows must not be used to resolve SOS/EOS durations unless a later spec explicitly says so.
- **Usual post times** (locating aid, not assignment): day-shift reports after ~14:00; night-shift reports after ~02:00. Both are ordinarily present well before the ~05:30 meeting. Supervisors post after their shift, not at 05:59.
- **Meeting time** (~05:30) is when the pack is consumed. It is not the reporting-cycle boundary and must not be used to compute work durations.
- **Sender aliases** (source label → person), persisting once known.
- **Item aliases** (e.g. STC9 = STC09), persisting once known.

Absence of an alias is not a reason to drop an entry.

### 1.3 Out of V1 input scope

Not consumed as V1 **report sources**:

- messages from **Jaco Fouché** (his day-shift / statutory / planned stream — later input);
- other departments’ reports and comments in the same group;
- control-room reports; safety/attendance systems.

Jaco Fouché’s messages must remain in the loaded source and must not be deleted, merged into a supervisor reporting unit, or reinterpreted as Lyle’s, Jurie’s, or Fanie’s report. They simply do not contribute V1 rows.

The input contract must remain open. “Supervisor WhatsApp messages” is the first stream, not the permanent definition of Atlas input.

---

## 2. Selection and filtering

Filter before interpretation. Do not interpret the whole shared feed.

### 2.1 Sender filter

Keep only messages from configured relevant senders. Drop other departments before interpretation.

Other departments may mention the same machines. That does not bring those messages into V1.

### 2.2 Reporting cycle (morning pack)

The operational reporting day runs **06:00 to 05:59 the following morning**.

That operational day contains:

- **Day shift** — approximately 06:00–14:00;
- **Night shift** — approximately 18:00–02:00 (crossing midnight, still the same operational day).

The ~05:30 meeting sits near the end of that operational day and **consumes** its picture. The meeting is not the cycle boundary.

Supervisors submit shortly after **their shift** ends, not at the end of the 06:00–05:59 cycle:

- day-shift reports normally after ~14:00;
- night-shift reports normally after ~02:00.

Both are ordinarily available well before 05:30. Submission time is how Atlas **finds** those messages. It does not define which operational day they belong to.

The pack for an operational day is the supervisor reporting units that **describe** that day’s day shift and night shift — the 24-hour operating period — not “messages whose calendar date is today,” and not “messages submitted since 05:30.”

**Assignment rule (unchanged):** assign a report to the operational day and shift it **describes**.

Evidence, in order:

1. What the report says — explicit labels (`Tmm night shift`, `Daily Report`, a date in the heading), and work times as written.
2. Submission timestamp — used to **locate** candidate messages (search around the usual post-shift times) and to **associate** a report when it is consistent with the described shift.

Submission time is not the assignment key.

**Late or missing reports are exceptions, not a subsystem.**

- If the pack has no reporting unit for the day shift, or none for the night shift, flag that report as missing.
- If a report arrives outside the usual post-shift window (including after 06:00) but still describes this operational day, keep it on this day and flag it as late.
- Do not build an as-of / point-in-time picture, a late-arrival pipeline, or historical replay machinery for uncommon late posts.

If the described day/shift cannot be established, flag the association; do not reassign solely because submission crossed 06:00.

Approximate shift windows may be used only as context for understanding which shift a report is about. They must not be substituted for SOS/EOS when calculating durations.

Calendar-date grouping of messages alone is forbidden.

### 2.3 Reporting units

A **reporting unit** is a supervisor shift report, not every message from that sender.

- Messages that contain operational report content (machine/item work, times, attendance, workshop/proactive notes, handover lines, and similar) are reporting units or parts of one.
- Acknowledgements or chatter with no operational content are not reporting units and are not interpreted as work.
- Follow-up messages from the same sender that continue the same report remain part of that unit when the continuation is clear. If association is unclear, keep them as extra source messages in the pack; do not drop them and do not invent a second shift.

Explicit day/night wording is the shift **label**. Submission time helps find and associate the unit. Neither rewrites work clocks inside the report, and neither moves a late report into the next operational day by itself.

### 2.4 Preserve then interpret

For every message kept in the pack, preserve the raw source (text, sender, timestamp, media references) before extraction.

---

## 3. Information Atlas may extract

Extract only what the pack supports. Missing fields stay missing.

Atlas may identify, as **claims taken from the text**:

| Area | May extract | If not in the text |
|---|---|---|
| Item | Machine/equipment identifier, other named item, or a non-machine heading (attendance, workshop, infrastructure, proactive bundle) | Keep a row keyed by a verbatim item/description; do not invent a machine |
| Identity notes | Obvious alias after applying configured aliases | Leave written form visible; do not drop |
| Timing | Start, end, as written; whether each side is numeric, SOS/EOS, open, or “still busy” | Times not established |
| Problem | Reported fault, symptom, or failure wording | Not established |
| Work | Diagnosis, work performed, parts, temporary repair, recovery, workshop work, testing, as written | Not established |
| Last reported state | A label grounded in the wording (see §6 and §9) | State not established from report |
| Follow-up | Outstanding work, parts still required, untested, NS/day-shift to continue, and similar | None stated |
| People | Names as written | Not established; do not invent roles |
| Work character | Breakdown / inspection / proactive / workshop / attendance / other, **only** when the wording supports it | Work character not established |
| Media | That media exists or is referred to; retain the reference | Do not assume none |
| Verbatim | Original phrases that were compressed (especially exceptions) | — |

One source report may yield several entries. One entry may be non-machine content. Do not force every line into a breakdown-shaped machine row.

Headings (`Tmm night shift`, `Daily Report`, `Absent`, `All at work`) provide **context** for the lines below them. They are not machine identifiers and must not receive machine-status semantics.

A line after a completed block (for example after `Running`) that describes other equipment is a **new** activity, not an attachment to the previous machine.

People, attendance, and workshop/proactive lines are first-class extractable content when present. Attendance rows use attendance wording, not last-reported machine state. If an attendance heading has no absenteeism listed, omit the row.

---

## 4. Deterministic calculations allowed

Only after the relevant values are already resolved as ordinary data (not guessed by a model).

Allowed:

- sender filtering using configuration;
- locating candidate messages using submission timestamps around the operational day (06:00–05:59);
- applying configured sender and item aliases;
- sorting and grouping **for display** (e.g. by normalised item identity);
- duration between two **numeric** start and end clocks already identified in the text;
- detecting numeric interval overlap **as a fact about reported work intervals** (for highlighting), not as downtime;
- rendering the table and exception list;
- retrieving preserved raw source and prior configuration.

Duration, when calculated, is labelled **reported work/activity interval**. It is not downtime.

If either bound is SOS, EOS, open-ended, “still busy,” missing, or otherwise non-numeric, **do not calculate a duration**. Preserve the original wording.

In a **night-shift** report, do not confidently calculate a duration when numeric clocks wrap midnight and the written start looks like a daytime clock (for example `10:30–00:10` yielding 13 h 40 min). Keep the original wording, withhold the duration, and flag for confirmation. Do **not** silently rewrite `10:30` to `22:30`.

Also withhold and flag a numeric wrap whose calculated duration is longer than 8 hours (for example `22h00–20h45` yielding ~22 h). Ordinary short midnight spans such as `22:30–01:55` may still be calculated.

Recognize a machine header glued to a time (`L9122h15 - 22h55` → L91, 22h15–22h55). Do not invent a machine `L9122`.

If one written machine block contains more than one work interval, keep each interval visible as its own entry (same machine unless the later work is clearly a different operational activity).

Report-level or orphan fragments (labor %, empty scotch/parts car, farm gates, stop-and-fix, long standing, standing-at-location notes, chatter replies) must not be attached to the previous machine. They survive as their own operational rows or are omitted if they are only a labor-percentage line.

When two same-item last-reported states are chronologically an unresolved state then a later closed/operational state, treat that as progression/closure, not an automatic conflict.

Meeting time, operational-day bounds, and approximate shift windows must not be used as SOS/EOS substitutes.

---

## 5. Conclusions Atlas may make

Atlas may:

- conclude that a message is or is not a reporting unit when that is clear from content;
- assign a reporting unit to the operational day and shift it describes, using the report’s wording first and submission time only as a locating/associating aid;
- extract the claims in §3 that are supported by the wording;
- normalise an identifier using a configured alias;
- calculate a reported work/activity interval from two numeric clocks;
- state that two numeric intervals on the same item overlap;
- state last reported state, labelled as reported, when the wording supports it;
- mark an item as unresolved / not tested / awaiting parts / still under repair when the wording supports it;
- propose possible continuity as a **flag**, e.g. “Possible continuation of earlier L91 work”;
- treat continuity as already one written entry only when the text itself states continuation so plainly that no interpretation is required (same reporting unit, explicit “continue/cont to …”, a follow-up that is clearly more text of the same entry);
- highlight ambiguity, missing clocks, media-not-interpreted, possible continuity, and conflicting statements in the pack;
- omit emphasis, escalation, and “what management cares about” — those stay human.

---

## 6. Conclusions Atlas must not make

Atlas must not:

- invent faults, repairs, people, times, machines, or status;
- treat Lyle, Jurie, Fanie, or Jaco as software agents or invent a sender when the source label is unclear;
- introduce an as-of / point-in-time view, or special machinery whose only purpose is uncommon late or missing reports;
- assign a report to the next operational day solely because it was submitted after 06:00, or to a pack solely because of calendar date or meeting time;
- resolve SOS/EOS/open ends to clock times, including by substituting 06:00, 14:00, 18:00, or 02:00;
- emit a duration unless both ends are numeric;
- call a calculated interval **downtime**, or treat work duration as machine downtime, unless the report explicitly establishes a downtime/unavailability period as such;
- turn “last reported operational / running / complete” into “the machine is currently running”;
- silently merge two entries;
- merge merely because two entries name the same item;
- drop an entry because it does not match a TMM prefix list or a schema;
- drop unusual, proactive, workshop, infrastructure, or attendance content;
- interpret images, voice notes, or video;
- pretend the text is complete when the report refers to photos/media;
- use other departments’ messages, control-room data, or the user’s own stream in V1;
- infer statutory compliance or safety certification;
- judge people or utilisation;
- reopen yesterday’s unresolved items unless they appear again in this pack;
- send, publish externally, or edit any system outside Atlas;
- replace or discard the raw source;
- manufacture confidence to finish the table.

---

## 7. Ambiguity and reconciliation

Reconstructing continuity without inventing relationships is required. Silent collapse is forbidden.

### 7.1 Default: entries stay independent

Each extracted entry remains independently visible in the output.

Atlas may **display-group** rows that share a normalised item identity. Grouping is visual only.

### 7.2 Possible continuity — flag, do not collapse

Same machine is not, by itself, evidence of continuity.

Flag possible continuation only when the wording actually supports it, for example:

- continue / cont to;
- still busy;
- a handover instruction (ask X to repair, night shift to, NS to);
- unresolved work carried forward (not complete, awaiting parts, and similar).

Example:

```text
Possible continuation of earlier L91 work — confirm
```

Both rows stay in the table. Overlapping intervals may still be noted as overlap without a continuity flag.

### 7.3 Explicit continuation only

Collapse into a single entry only when the source already writes one continuing activity so that joining them is transcription, not judgement.

If there is any doubt, do not collapse.

### 7.4 Overlap

If two numeric work intervals on the same item overlap, Atlas must not add the durations. It may show each interval and note the overlap. It must not emit a machine-level downtime total from that overlap.

### 7.5 Conflict and uncertainty

If statements in the pack conflict, or status/times/item identity cannot be established, say so. Prefer:

```text
State not established from report
```

```text
Reported period incomplete (sos – 14h30)
```

over a plausible guess.

### 7.6 What must be highlighted (exception review)

Highlight at least:

- possible continuity (proposed, not merged) when wording supports it;
- SOS/EOS/open-ended/incomplete times;
- ambiguous night-shift clocks whose duration was withheld;
- unresolved / not tested / awaiting parts / still under repair;
- media referenced or attached but not interpreted;
- item identity uncertain or unaliased and ambiguous;
- conflicting statements about the same item in the pack;
- sender or role uncertain (do not guess an author);
- operational day or shift association uncertain;
- expected day-shift or night-shift report missing from the pack;
- late-posted report (arrived after the usual post-shift window) that was kept with the described cycle;
- any row that required non-trivial interpretation to extract.

Do not highlight ordinary rows whose item, numeric period, work, and last reported state are directly supported by the text.

`State not established from report` remains a valid **row state** when the source does not support a stronger label. It is not, by itself, a high-priority exception.

---

## 8. Correction behaviour

### 8.1 Configuration-type corrections — persist

These survive into later mornings:

- item aliases (STC9 = STC09);
- sender aliases;
- additions/changes to the relevant-sender time bounds.

The user must not have to re-teach the same alias every morning.

### 8.2 Interpretation corrections — this output only

The user may correct the current table (wording, state label, “not related” on a possible-continuity flag, split/keep independent, etc.).

V1 does not turn a single interpretation correction into a standing merge/split rule.

### 8.3 Source protection

Corrections must not destroy or overwrite the raw source. The original message remains accessible after correction.

### 8.4 No extra administration

V1 must not introduce a daily capture, confirmation, or register-maintenance ritual beyond: run/open the pack, scan exceptions, correct what is wrong. Supervisors do not change reporting behaviour.

---

## 9. V1 output contract

One working morning table, not a separate management summary and not a second technical product.

### 9.1 Columns

| Machine / Item | Reported period | What happened | Work / finding | Last reported state | Follow-up / unresolved |
|---|---|---|---|---|---|

- **Machine / Item** — machine id, other equipment, or a non-machine label (attendance, workshop, infrastructure, verbatim topic). Not only TMM prefixes.
- **Reported period** — as written; calculated interval only as a supplement when both clocks are numeric, labelled as reported work/activity interval.
- **What happened** — compressed problem/event wording; technical meaning retained; original phrasing recoverable.
- **Work / finding** — what was done or found.
- **Last reported state** — always as a *reported* claim, e.g. Reported operational; Reported complete; Not tested; Still under repair; Awaiting spares; State not established from report. Never implied live state.
- **Follow-up / unresolved** — only what this pack states or leaves open. Empty if nothing is open **in this pack**.

### 9.2 Also available with the table

- immediate access to the underlying raw report(s) for any row;
- exception list / highlights (§7.6);
- media markers and references, without interpretation;
- pack identity: which operational day (06:00–05:59), which senders, which reporting units, and that the ~05:30 meeting is the consumption time for that day.

### 9.3 Rows

- One row per extracted entry, including non-machine content that was in the reports.
- Display-grouped by item when useful; not merged.
- No requirement to fill every cell; unstated facts stay unstated.

### 9.4 Carry-over

Only unresolved items found **in this pack**. V1 does not automatically reopen yesterday’s open items unless they occur again in the current input.

---

## 10. Acceptance tests

These are behavioural tests. They pass or fail on outputs, not on internals.

### Pack selection

1. **Night after midnight, still same operational day.** A relevant supervisor night report submitted at 03:54 (after midnight, before 06:00) that describes the night shift just completed is in that operational day’s pack, not excluded for being “today.”
2. **Day report posted after day shift.** A relevant supervisor day report submitted at 15:50 on the calendar day the day shift ran is in that operational day’s pack.
3. **Next operational day’s day report excluded.** A relevant supervisor day report that describes the *following* day’s day shift is not in this pack.
4. **Late post does not change day.** A relevant supervisor report that describes last night’s night shift but is submitted at 06:30 remains in the operational day that ended 05:59, flagged late. It must not appear only in the new operational day that started 06:00. No as-of store is required.
5. **Meeting is not the boundary.** A relevant night report submitted at 05:40 (after the ~05:30 meeting, before 06:00) still belongs to the operational day that ends 05:59. Usual night posts are after ~02:00 and before the meeting; 05:40 is an exception to flag if treated as late, not a new cycle.
5a. **Missing shift report.** If an operational day’s pack has a day-shift reporting unit and no night-shift reporting unit (or the reverse), the output flags the missing shift. It does not invent a report or open a replay workflow.
6. **Other departments dropped.** A long operational report from a non-configured sender is not interpreted and contributes no rows.
7. **No TMM-prefix filter.** An in-pack supervisor entry for a farm gate, workshop rebuild, ADR/SEC/ARB-style item, or attendance is present in the table.
8. **Fanie tenure.** Replay of a morning during Fanie’s configured tenure includes Fanie and does not require Jurie. Replay after Jurie’s configured start includes Jurie. Overlap days include both if both posted.

### Extraction honesty

9. **No invented clocks.** `sos – 14h30` appears with original wording, no fabricated start, no duration. Shift windows (06:00/14:00/18:00/02:00) are not substituted for SOS/EOS.
10. **Numeric duration only.** `20h40 - 21h45` may show a reported work/activity interval of 1 h 05 min, not labelled downtime.
11. **Still busy.** `09:30-Still busy` has no calculated duration.
12. **Verbatim exception.** `No operator to test` remains recoverable and drives a not-tested / unresolved outcome, not “operational.”
13. **Media marker.** Text that says photos were posted, or a message with attached/omitted media, marks media present and does not treat the text as complete.
14. **User stream absent, not reassigned.** Messages from Jaco Fouché produce no V1 rows, remain in the loaded source, and are not treated as a Lyle/Jurie/Fanie reporting unit.
14a. **Unclear sender.** A message whose source label is not a configured relevant sender (and is not a configured alias) is not interpreted as a supervisor report. If it was a candidate only because the label is ambiguous, flag sender/role; do not guess.

### Reconciliation

15. **Same item, two jobs.** Two same-machine entries without explicit continuation remain two rows. A possible-continuity flag is allowed; silent merge is not.
16. **L91-style related work.** Inspection/handover plus a later repair on the same item stays two visible rows with a possible-continuity flag unless the source text itself is one continued entry.
17. **Overlap not summed.** Same item, `09:00–13:30` and `12:00–13:30`, must not become 6 h of anything. Overlap may be noted. No downtime total.
18. **Explicit continue.** “Cont to assemble” in a single written activity is one entry, not two incidents.
19. **Conflict.** Two incompatible last-states for the same item in the pack remain visible; Atlas does not pick a winner.

### Status and wording

20. **Reported, not live.** Wording such as `Running` becomes a last-reported label (e.g. Reported operational), never “currently running.”
21. **Work interval ≠ downtime.** A calculated numeric interval is not called downtime without explicit downtime language.
22. **Technical meaning.** A compressed “what happened / work” pair still contains the fault and the actual repair/finding, not only “repaired.”

### Corrections and friction

23. **Alias persists.** After STC9 = STC09 is recorded, the next pack does not treat them as two unexplained identities.
24. **Interpretation correction does not become a merge rule.** Rejecting a possible L91 link does not require a general learned rule; it must correct **this** table and leave the raw source intact.
25. **No supervisor change.** Acceptance uses the reports as written. A test that requires added headings, codes, or downtime fields from supervisors is invalid.
26. **Exception-only review.** Ordinary directly supported rows are unflagged. Flags are reserved for §7.6 cases.
27. **Deck-chair.** If producing or correcting the table for a real morning takes as long as compiling from the raw reports, V1 has failed — even if the table looks complete.

---

## 11. Explicit non-goals (V1)

- Live WhatsApp monitoring.
- Image, voice, or video interpretation.
- Control-room comparison.
- Persistent cross-day action tracking.
- The user’s day-shift / statutory stream.
- A second management-summary product.
- Learned merge rules.
- Shift-clock resolution of SOS/EOS.
- An as-of / point-in-time subsystem or late-arrival pipeline.
- Any external send or system update.

Those may be added later as separate earned responsibilities, through the same replaceable-input pattern.
