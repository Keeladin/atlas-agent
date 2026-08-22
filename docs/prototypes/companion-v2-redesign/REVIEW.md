# Companion v2 visual reset — review package

**Status:** visual prototype only — not the running Companion  
**Running product:** `companion/src` served by `atlas_api`  
**This folder:** design explorations and stills under `docs/prototypes/`. Do not treat screenshots or HTML mocks as current implementation.

## Open the proposals

- Gallery: `docs/prototypes/companion-v2-redesign/index.html`
- Local server (if running): http://127.0.0.1:8767/
- Interactive screens: `screens/*.html`
- Rendered mood / concept frames: `assets/*.jpg`

## Screens

| Screen | Intent |
|---|---|
| Home | Attention / command centre: Needs you, In motion, Continue, Quick starts |
| Chat | First-class conversation; context rail; Start work from thread |
| Work list | Human titles + status chips + progress; no IDs |
| Work detail | Decision cards, timeline, artifacts/evidence/activity; Inspect for runtime |
| Plan Work | Intent → readable plan review → Accept (not JSON-first) |
| Mobile | Same system; bottom nav; decision-first work |

## Non-goals in this proposal

- Polishing the current harness layout
- Changing API contracts or runtime semantics
- Shipping production CSS into Companion before approval
