# Atlas product direction (not V1)

Status: **direction only**. This is not a V1 requirement and does not change the completed morning-workflow slice.

Do not implement this from this note. Do not add supervisor accounts, forms, authentication, a database, or WhatsApp integration because of it.

---

## Why this is recorded now

Jaco uses the morning operational picture from a **phone**. Supervisors will eventually **author** shift reports on a phone. Future UI and output decisions must not assume a desktop-only consumer or WhatsApp-composed source text as the permanent shape of Atlas.

V1 still consumes existing WhatsApp exports. That remains correct until this direction is earned as its own slice.

---

## Mobile use of the operational picture

The morning table must remain usable on a phone: readable, scannable, exception-first, with raw report text still reachable.

Do not design later surfaces that only work as a wide desktop spreadsheet or that bury the pack behind a desktop chat console.

---

## Supervisor reporting: Atlas authors, WhatsApp still publishes

Longer term, Lyle and Jurie should complete their shift reports **in Atlas on a phone**, not compose them first in WhatsApp.

Intended human workflow:

1. The supervisor completes the shift report in Atlas (mobile).
2. Atlas keeps the structured record and the original wording internally.
3. Atlas produces a **plain-text** version formatted for the existing shared engineering WhatsApp group.
4. The supervisor taps **Copy**.
5. The supervisor pastes that text into the group themselves.

Reporting in that WhatsApp group remains an **official requirement**. Atlas does not send, post, or bot-publish on their behalf in this direction.

Atlas becomes the **controlled source** of the report. WhatsApp remains an **external reporting channel**.

---

## Constraints this imposes on later work

- Mobile is a first-class way to read the pack and, later, to write a report. It is not a resize of a desktop app.
- Keep a clean split between **internal report** (what Atlas stores) and **external plain text** (what is copied into WhatsApp).
- Do not assume WhatsApp message text is the only or permanent system of record.
- Do not replace the group’s official paste-in ritual with silent automation unless a later, explicit decision says so.
- Supervisor-authored reports are still human operational reports, not agent personas.

V1 acceptance tests, filters, operational-day rules, and the export adapter stay as they are until a future slice is specified.
