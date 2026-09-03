# Atlas Companion (owner UI)

Canonical owner interface for Atlas. Served by `atlas_api` from `companion/dist`. The default `/chat` route is the owner surface: conversation, runtime readiness, Active Work, Cadence, Needs you and Control awareness are co-visible; Work, Sources, Memory, Operations and Control remain deeper routes.

```bash
npm ci
npm run build
```

Then run the API from the repository root (see the root README). Do not treat this package as a standalone Vite demo.

Secrets never belong in this UI. Provider keys stay on the host via `CredentialStore`.

The installed PWA identity is also source-controlled here. `public/manifest.webmanifest`, `favicon.svg`, `pwa-192.png`, `pwa-512.png` and the document theme colour must stay aligned with the current blue/cyan/violet Atlas mark. The old gold globe is legacy startup artwork; after asset changes, remember that installed PWAs/service workers may retain stale launch resources until cache/update lifecycle completes.


The Atlas Capabilities screen is inventory-driven. It groups native and discovered MCP/n8n capabilities from `/api/system`, renders inputs from each capability's `input_schema`, and invokes the generic capability route. New provider tools should normally become visible and usable without a Companion code change. Complex schemas retain an advanced JSON fallback; runtime validation and policy remain authoritative.
