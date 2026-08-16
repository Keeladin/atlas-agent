# Atlas Morning Workflow — Implementation Plan v1

Status: **accepted**  
Spec: `Atlas Morning Workflow — Behavioural Specification.md` (frozen)

This is the smallest plan that can satisfy the acceptance tests against the real WhatsApp export. It is a procedure, not a platform.

---

## Not in this plan

Live WhatsApp, control-room, management dashboard, cross-day carry-over, media interpretation, model routing, agents, database, web app, job queue.

If a step can be a function plus a config file, it is not a new subsystem.

---

## Shape

One local Python program. One config file. One aliases file. stdin/path in, working table out (Markdown is enough). `unittest` against fixture excerpts cut from the real export.

Replaceable input = one function that turns a source into a list of messages `{sender, timestamp, text, media_refs}`. First adapter: WhatsApp export text (`DD/MM/YYYY, HH:MM - Sender: …`, multiline until the next header). A zip that contains that text is unwrapped in the same function, not a second product.

Relevant-sender config names **human authors**, not software identities: Lyle; Jurie Venter; Fanie Lombard (tenure-bounded). Jaco Fouché is loaded with the export and excluded from V1 rows — not deleted, not folded into a supervisor unit. Other senders (including Jaco Robberts and other departments) are ignored for V1 even when they name the same machines. An unmatched or ambiguous sender label is flagged, never guessed into Lyle/Jurie/Fanie/Jaco.

---

## Config (data, not code)

A single file, edited when aliases or tenure change:

- relevant senders with optional `from` / `until` (Lyle unbounded; Fanie and Jurie bounded for replay)
- operational-day start `06:00` (end is the next 05:59)
- approximate shift windows only as assignment context, never as duration clocks
- usual post times as a locating aid only: day reports after ~14:00, night reports after ~02:00 (both expected before ~05:30)
- meeting time recorded as documentation only (~05:30)
- no as-of snapshot, no late-arrival store
- sender aliases and item aliases (same file or a sibling file loaded with it)

Initial tenure bounds are **config values taken from the export**, not hardcoded rules. Observed in the export for the first file: Fanie posts through ~2026-04-30; Jurie from ~2026-04-28. Overlap days keep both if both are configured active.

---

## 1. Input loading

Parse the export into messages. Preserve raw text exactly. Detect media as:

- `(file attached)` / named `IMG-` `PTT-` `VID-` lines;
- `<Media omitted>`;
- wording such as “photos on report group”.

Do not open or interpret media files.

Tests: header/multiline parse; system line skipped; media markers recorded; timestamps as source clocks.

---

## 2. Sender / report filtering

**Before any interpretation of body content:**

1. Keep messages whose sender matches the relevant-sender list **at that timestamp**.
2. Drop everyone else, including other departments that name TMM machines, and including Jaco Fouché (exclude from V1 rows only; do not delete the message or attribute it to a supervisor).
3. If a source label is not a configured sender or alias and looks like it might be a supervisor nickname, **do not guess**. Leave it out of V1 authors and flag sender/role if it was otherwise a reporting-unit candidate.

**Reporting units** (still no model):

- Keep a message if it has operational report shape: named item/machine-like token, time range, attendance/absent block, or an explicit report heading (`Tmm night shift`, `Daily Report` / `Dialy Report`, `Day Shift`, `Night shift`).
- Drop short chatter with none of that.
- Prefer include when unsure.
- Same-sender messages that follow a report and look like continuation (more items/times, or media) stay attached as extra source for that unit. If unclear, keep as extra source in the pack, not a second shift.

No TMM-prefix allowlist.

---

## 3. Operational-day assignment

Identify an operational day as the date of its **06:00 start**.  
Operational day `2026-03-25` = 06:00 on 25 Mar through 05:59 on 26 Mar. The 05:30 meeting on 26 Mar consumes that day.

Assign each reporting unit to the day/shift it **describes**:

1. **Text first:** heading date; `night` / `day` / `Tmm night shift` / `Daily Report`; work clocks as written (numeric only).
2. **Submission time second:** locate candidates around the usual post-shift times (after ~14:00 for day, after ~02:00 for night). Associate when consistent with (1). Do not wait for 05:59.
3. **Late post:** uncommon. If text describes the night shift that just ended and submission is after 06:00, keep the operational day that **ended** at 05:59 and **flag late**. Do not roll forward. Do not add an as-of or replay path.
4. **Missing shift:** if the pack has no day-shift unit or no night-shift unit, **flag missing**. Do not invent a report.
5. If day/shift cannot be established: include in the located pack if a candidate, **flag** association. Do not assign on 06:00 crossing alone.

A `pack(operational_day)` is: all reporting units assigned to that day (its day shift + its night shift).

Do not use 06:00 / 14:00 / 18:00 / 02:00 to fill SOS/EOS.

---

## 4. Extraction

One function: reporting-unit text → list of **entries**. No second “technical” extract.

Each entry is a plain record:

- `item` (machine, other equipment, or non-machine label; verbatim if unusual)
- `period_raw`, `start`, `end`, `start_kind`, `end_kind` (`numeric` | `sos` | `eos` | `open` | `still_busy` | `missing`)
- `what_happened`, `work_finding`
- `last_reported_state` (reported-* labels only, or `state not established`)
- `follow_up`
- `people` (names as written)
- `work_character` only if wording supports it, else unset
- `media_present`, `source_ref` (pointer to raw message)
- `verbatim_exceptions` (e.g. “No operator to test”)

Rules enforced **after** the function returns (so a model cannot violate them):

- empty/missing stays empty;
- no invented clocks;
- `Running` / `complete` map to **reported** labels, never live status;
- user stream never appears (already filtered);
- no TMM-prefix drop.

How the function is filled: start with deterministic chunking of the common “item / time / lines” blocks seen in Lyle/Jurie/Fanie reports; send only leftover ambiguous prose through a single interpretation call if needed. If the interpretation call is unavailable, keep the block as one entry with verbatim text and `state not established` rather than skip it. That is not a router and not an agent.

---

## 5. Deterministic interval calculation

Separate function. Input: `start`, `end`, kinds.

- Both `numeric` → `reported_work_interval` (e.g. 1 h 05 min). Label **must** be work/activity interval, never downtime, unless the source text explicitly says downtime/unavailable for that span (then a separate field; default off).
- Any SOS/EOS/open/still_busy/missing → no duration; `period_raw` unchanged.
- Crossing midnight with two numeric clocks (e.g. 22:30–01:55) is ordinary clock arithmetic, not shift-window substitution.

Overlap: if two entries share a normalised item and both have numeric intervals that overlap, set `overlap_noted` on both. Do not add durations. Do not emit a machine downtime total.

---

## 6. Conservative reconciliation / flagging

Never merge rows.

Display-group only: sort/group by aliased item identity for the table.

Flags (and only these, plus spec §7.6):

- **possible continuity** — same item, no explicit continue, and (handover language or overlapping/adjacent numeric times or “ask X to repair” / “NS to …”). Both rows stay.
- **explicit continue** — a single written activity (`Cont to assemble` in one block) stays one entry because extraction kept one block, not because a merger ran.
- **conflict** — two last-reported states for the same item in the pack; both rows stay; no winner.
- incomplete times, state not established, unresolved/not tested/awaiting parts, media not interpreted, uncertain item, uncertain day/shift, late post kept with described cycle, non-trivial interpretation.

Ordinary rows with item + two numeric clocks + work + supported reported state: **no flag**.

Rejecting a possible-continuity flag is a correction to **this pack’s output**, not a saved merge rule.

---

## 7. Working-table output

One Markdown table:

`Machine / Item | Reported period | What happened | Work / finding | Last reported state | Follow-up / unresolved`

Plus, immediately below or beside:

- exception list (the flags);
- raw source for each row (message sender/time + full text);
- media markers;
- pack header: operational day 06:00–05:59, senders, reporting units used.

No second management document.

---

## 8. Correction / alias behaviour

- **Persist:** append/update item aliases and sender aliases in the aliases file. Next `pack()` applies them. Test: STC9 = STC09 then both display as one identity, still two rows if two jobs.
- **This pack only:** write a small corrections sidecar for that operational day (edit a state label; dismiss a possible-continuity flag). Regenerating the table applies the sidecar. Do not generalise a dismissed L91 link into a rule.
- Raw messages stay untouched.

No register UI. Editing the aliases file (or a one-line “add alias” command) is enough.

---

## 9. Acceptance tests

`unittest`. Fixtures are **excerpts** of the real export (plus a few synthetic lines for late-post 06:30 / 05:40 if that exact pair is not in the file). Do not require the full zip in git.

| Tests | How |
|---|---|
| 1–5 pack assignment | Known Lyle/Jurie/Fanie messages with real timestamps; assert operational-day id |
| 4 late 06:30 | Night-labelled report at 06:30 → day that ended 05:59, flagged late; no as-of object |
| 5 post-meeting 05:40 | Same operational day, not a meeting-bounded cycle |
| 5a missing shift | Pack with only a day report flags missing night report |
| 6 other departments | Francois / numbered senders present in excerpt, zero rows |
| 7 no prefix filter | Fanie farm-gate / ADR / attendance row present |
| 8 Fanie tenure | One pack in Fanie window includes Fanie; one after includes Jurie; overlap includes both if both posted |
| 9–11 clocks | Direct interval-function tests + one real `sos` line |
| 12 not tested | Real or excerpt phrase survives; state is not operational |
| 13 media | Omitted/attached/`photos` → `media_present` |
| 14 user stream | Jaco Fouché message in excerpt → no rows; still present in loaded source; not attributed to Lyle/Jurie/Fanie |
| 14a unclear sender | Unknown label is not guessed as a supervisor |
| 15–16 no silent merge | Two L91-style blocks → two rows; flag allowed |
| 17 overlap | 09:00–13:30 and 12:00–13:30 → no 6 h, no downtime total |
| 18 explicit continue | `Cont to assemble` single block → one row |
| 19 conflict | Two states, both visible |
| 20–21 reported / not downtime | Output strings |
| 22 technical meaning | Fixture where compression still contains fault + finding |
| 23 alias persists | Aliases file round-trip |
| 24 interpretation correction | Sidecar dismisses flag; no new global rule file |
| 25 supervisor format | All fixtures are unmodified export text |
| 26 exception-only | Ordinary numeric complete row unflagged |
| 27 deck-chair | Not automated. After a real morning pack is produced, you judge. Failure if you still read every line to trust it. |

Run order when implementing: **1–6, 8, 9–11, 14, 15–17, 23** first (purely deterministic). Then extraction (7, 12, 13, 18–22, 26). Then sidecar (24). Then one full real operational day from the export (27, human).

---

## Build order

1. Message loader + fixture excerpts.
2. Sender filter + tenure config + “user not in list.”
3. Reporting-unit keep/drop (inclusive heuristics).
4. Operational-day assignment + tests 1–5, 8.
5. Interval function + tests 9–11, 17, 21.
6. Entry records + alias apply + table render.
7. Extraction (chunk first, one interpretation fallback) + postcondition guards.
8. Flagging only (no merge) + tests 15–16, 19, 26.
9. Corrections sidecar + test 24.
10. One real operational-day pack from the export; you review test 27.

Stop after that. Do not add another input, store, or interface unless a test cannot be met.

---

## Risk (not a spec change)

Unlabelled late posts are uncommon. If text vs 06:00 conflicts, stop and reopen the spec. Until then: text wins, flag late or missing, never roll forward on the clock alone, and do not add as-of machinery.
