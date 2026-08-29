# Atlas Companion (owner UI)

Canonical owner interface for Atlas. Served by `atlas_api` from `companion/dist`.

```bash
npm ci
npm run build
```

Then run the API from the repository root (see the root README). Do not treat this package as a standalone Vite demo.

Secrets never belong in this UI. Provider keys stay on the host via `CredentialStore`.

The Atlas Capabilities screen is inventory-driven. It groups native and discovered MCP/n8n capabilities from `/api/system`, renders inputs from each capability's `input_schema`, and invokes the generic capability route. New provider tools should normally become visible and usable without a Companion code change. Complex schemas retain an advanced JSON fallback; runtime validation and policy remain authoritative.
