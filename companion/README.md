# Atlas Companion (owner UI)

Canonical owner interface for Atlas. Served by `atlas_api` from `companion/dist`.

```bash
npm ci
npm run build
```

Then run the API from the repository root (see the root README). Do not treat this package as a standalone Vite demo.

Secrets never belong in this UI. Provider keys stay on the host via `CredentialStore`.
