# Atlas Mobile Shift Reporting — Proposal for Review

**Queue status:** queued for review. No implementation authorised. V1 parser/replay unchanged.
Purpose
Replace free-form end-of-shift report composition with a lightweight structured mobile capture flow.
The goal is not to make supervisors complete more administration. It is to remove the ambiguity Atlas currently has to reconstruct from free-form WhatsApp reports.
Historical replay has shown that reports naturally contain inconsistent formatting, omitted times, multiple jobs merged together, malformed machine/time headings, logistics mixed with machine work, and changing machine states. These are normal human reporting conditions, especially when people are tired or rushed.
Rather than trying to make the legacy parser infinitely clever, Atlas should eventually capture the important structure at source.
Core interaction model
The supervisor records one activity at a time.
A single entry screen contains only the information needed for that activity, for example:
Machine / item
Start time
End time, Still busy, EOS, or other supported open state
What happened / work performed
Last reported state
Follow-up / unresolved work
Optional free-text detail
People/media later where useful
The supervisor completes one entry and presses Next.
Atlas saves the activity and resets the form for the next entry.
The supervisor repeats this until the shift report is complete.
Validation before Next
Atlas validates each activity before allowing the supervisor to proceed.
Green — valid
The entry is structurally valid and internally consistent.
Next is enabled.
Orange — ambiguous or suspicious
Examples:
an unusual but possible time interval;
10:30 → 00:10 during night shift;
an unclear continuation;
a time sequence that may be a typo;
information requiring human confirmation.
Atlas must not silently correct it.
The supervisor either changes the value or explicitly confirms that it is correct. Once resolved, the entry becomes green and Next becomes available.
Red — invalid
Examples:
malformed time;
impossible field combination;
required structural information missing;
a value Atlas cannot safely interpret as structured data.
Next remains unavailable until corrected.
Principle
Ambiguity should not leave the entry screen unresolved.
The person creating the information is normally the person best placed to resolve it.
Atlas should therefore ask for clarification at capture time instead of guessing hours later during report reconstruction.
Continuation and handover
Where Atlas already has an unresolved previous activity for the selected machine, it should eventually show that context.
For example:
ARB4 — previous state: fitting broken / still under repair
The supervisor can explicitly choose:
Continue previous job
New issue
If continuing, the new activity becomes part of the existing operational thread.
A later result such as:
Remove broken fitting → Test all OK → Running
can then close the earlier unresolved state explicitly rather than appearing later as a supposed conflicting state.
End Report
When all activities are entered, the supervisor selects End Report.
Atlas then assembles the structured activities into a shift report and:
groups activities by machine/item;
orders work chronologically;
preserves separate jobs where appropriate;
calculates valid reported work intervals;
handles multiple intervals for the same machine;
identifies the last reported state;
surfaces unresolved work;
separates machine work from logistics, attendance and other operational activity;
produces a concise shift summary;
generates the required WhatsApp-ready text.
The supervisor sees the complete report for review before submission.
The review stage is therefore primarily confirmation of a clean report, not reconstruction of ambiguous source material.
Offline-first requirement
Underground network coverage cannot be assumed.

Supervisor report capture, validation, draft storage and End Report review must work without a server connection.

Reports are stored locally on the device until connectivity becomes available.

When network coverage returns, Atlas synchronizes the completed report to the server and clearly indicates sync status.

Loss of connectivity must not lose, duplicate or silently alter a report.

Connectivity is required for synchronization, not for report creation.

WhatsApp relationship
WhatsApp remains the required external reporting channel for now.
Atlas becomes the structured internal source.
Proposed flow:
Atlas mobile capture → End Report → Review → Generate WhatsApp report → Copy → Supervisor pastes into official WhatsApp group
No automatic WhatsApp sending is required for the first implementation.
Legacy parser
The existing WhatsApp parser remains valuable for:
historical data;
imported reports;
transition from the current process;
reports created outside Atlas;
resilience/fallback.
It should remain a conservative best-effort interpreter.
Its purpose is not to eventually infer perfectly every possible free-form reporting style.
Product constraint
The mobile flow must not become a large administrative form.
The supervisor should deal with one job at a time, with minimal required structure and space for natural free-text detail.
The intended experience is:
record what happened → resolve anything unclear → Next
not:
sit at the end of shift and complete another management form.
Proposed status
Future behaviour — queued for review. No implementation authorised yet.
Current V1 historical replay/parser work remains unchanged.