# Legacy Companion adapter

This package is **not** the canonical Atlas Companion.

Canonical Companion:

- UI: `companion/`
- API / composition root: `atlas_api` (`python -m atlas_api`)

`atlas_companion` remains only because:

- `CredentialStore` and `ProviderStateStore` / `build_registry` are still the host-local provider overlay used by `atlas_api`;
- `tests/test_companion_pwa.py` still covers this HTTP adapter.

Do not advertise `python -m atlas_companion.server` as the product. Work execution on this adapter is still disconnected by design.