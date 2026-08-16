Atlas Product Definition — Draft v0.1
Purpose
Atlas is a persistent operational agent whose job is to reduce recurring administrative, informational, and decision-making friction.
Its value should come from owning useful responsibilities over time, not merely answering prompts.
The first version should prove that Atlas can take responsibility for a small number of real workflows before we expand the platform.
Responsibility 1 — Maintain the current operational picture
Atlas should maintain an up-to-date representation of what is happening now.
For the first practical implementation, this could mean the engineering environment:
current machine status;
active breakdowns;
unresolved actions;
incidents and safety items;
work completed during recent shifts;
important changes since the previous reporting cycle;
information that still needs confirmation.
The user should not have to reconstruct this picture manually every time it is needed.
Definition of done
When asked, Atlas can answer:
“What is the current engineering situation?”

from persisted operational state rather than requiring the user to re-upload or re-explain everything.
Eventually this responsibility may also run automatically before recurring events such as the morning meeting.
Responsibility 2 — Own follow-up and outstanding work
Atlas should maintain a durable record of things that still require attention.
These may include:
open maintenance actions;
safety actions;
inspection findings;
training requirements;
commitments;
outstanding information;
reminders;
follow-up dates;
decisions waiting for someone else.
An action should survive beyond the conversation in which it was created.
Definition of done
Atlas can reliably answer questions such as:
“What still needs my attention?”

“What was supposed to be completed this week?”

“Which items have been outstanding longest?”

And it can surface relevant items when their context becomes important.
Responsibility 3 — Build operational memory
Atlas should accumulate useful history instead of treating each event as isolated.
For equipment, this could eventually connect:
machine → fault → symptoms → repair → parts → downtime → people involved → previous occurrence → relevant technical information
Documents and manuals should form part of this memory, but simply indexing documents is not enough.
The goal is to connect technical reference material with actual operational history.
Definition of done
Atlas can answer questions such as:
“Has this machine had this problem before?”

“What was done last time?”

“Are we seeing a recurring failure pattern?”

“Which part of the manual is relevant to this fault?”

Its answers should distinguish recorded facts from inference.
Responsibility 4 — Produce useful outputs and complete bounded work
Atlas should not stop at analysis when the required outcome is something it is authorised to produce or execute.
Depending on authority, this may include:
generating reports;
creating documents;
updating internal records;
assembling meeting briefs;
drafting or sending communications;
retrieving supporting information;
producing action lists;
preparing forms or logs.
The user should not have to manually bridge every step between knowing what needs to happen and getting it done.
Definition of done
For an authorised bounded task, Atlas can move from:
request or trigger → gather context → perform work → produce/execute result → record what happened
without unnecessary prompting between each step.