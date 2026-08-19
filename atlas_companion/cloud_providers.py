from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from atlas_companion.credentials import CredentialStore
from atlas_core.providers import ModelRouter, ProviderScoreStore, load_provider_registry_from_data


MANAGEABLE_CLOUD = {
    "xai:expert": {
        "vendor": "xai",
        "base_url": "https://api.x.ai",
        "default_env": "XAI_API_KEY",
    }
}


class CloudProviderError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_json(url: str, headers: dict[str, str], timeout: float = 20.0) -> Any:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise CloudProviderError(f"HTTP {exc.code} from provider.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CloudProviderError("Provider request failed.") from exc


def xai_list_models(api_key: str, *, base_url: str = "https://api.x.ai") -> list[str]:
    data = _get_json(
        base_url.rstrip("/") + "/v1/models",
        {"Authorization": f"Bearer {api_key}"},
    )
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise CloudProviderError("Provider returned no model list.")
    models: list[str] = []
    for item in rows:
        if isinstance(item, dict) and item.get("id"):
            models.append(str(item["id"]))
    return models


class ProviderStateStore:
    def __init__(self, root: str | Path) -> None:
        self.path = Path(root) / "provider-state.json"

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"providers": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"providers": {}}
        providers = data.get("providers")
        if not isinstance(providers, dict):
            return {"providers": {}}
        return {"providers": providers}

    def get(self, provider_key: str) -> dict[str, Any]:
        item = self.load()["providers"].get(provider_key)
        return dict(item) if isinstance(item, dict) else {}

    def update(self, provider_key: str, **fields: Any) -> dict[str, Any]:
        data = self.load()
        current = dict(data["providers"].get(provider_key) or {})
        current.update(fields)
        data["providers"][provider_key] = current
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return current


def overlay_providers(path: str | Path) -> dict[str, dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        str(key): dict(value)
        for key, value in dict(data.get("providers") or {}).items()
        if isinstance(value, dict)
    }


def apply_state_to_overlay(
    overlay: dict[str, dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    stored = (state.get("providers") or {}) if isinstance(state, dict) else {}
    for key, item in overlay.items():
        copy = dict(item)
        extra = stored.get(key) if isinstance(stored.get(key), dict) else {}
        if "enabled" in extra:
            copy["enabled"] = bool(extra["enabled"])
        if extra.get("model"):
            copy["model"] = str(extra["model"])
        if extra.get("base_url"):
            copy["base_url"] = str(extra["base_url"])
        merged[key] = copy
    return merged


def build_registry(
    overlay_path: str | Path,
    *,
    credentials: CredentialStore,
    state: ProviderStateStore,
):
    overlay = overlay_providers(overlay_path)
    merged = apply_state_to_overlay(overlay, state.load())
    for key, item in merged.items():
        env_name = item.get("api_key_env")
        if env_name:
            credentials.apply_env(key, str(env_name))
    return load_provider_registry_from_data({"providers": merged})


def rebuild_router(
    overlay_path: str | Path,
    *,
    db_path: str | Path,
    credentials: CredentialStore,
    state: ProviderStateStore,
) -> ModelRouter:
    scores = ProviderScoreStore(db_path)
    scores.initialize()
    return ModelRouter(
        build_registry(overlay_path, credentials=credentials, state=state),
        score_store=scores,
    )


def public_cloud_status(
    overlay_path: str | Path,
    *,
    credentials: CredentialStore,
    state: ProviderStateStore,
) -> list[dict[str, Any]]:
    overlay = overlay_providers(overlay_path)
    stored = state.load()["providers"]
    rows: list[dict[str, Any]] = []
    for key, item in overlay.items():
        if item.get("local"):
            continue
        extra = stored.get(key) if isinstance(stored.get(key), dict) else {}
        env_name = str(item.get("api_key_env") or "")
        file_configured = credentials.configured(key)
        env_configured = bool(env_name and os.environ.get(env_name))
        configured = file_configured or env_configured
        source = "secret_file" if file_configured else ("environment" if env_configured else None)
        enabled = bool(extra["enabled"]) if "enabled" in extra else bool(item.get("enabled", False))
        model = str(extra.get("model") or item.get("model") or "")
        rows.append(
            {
                "key": key,
                "kind": item.get("kind"),
                "local": False,
                "base_url": item.get("base_url"),
                "configured": configured,
                "source": source,
                "enabled": enabled,
                "selected_model": model,
                "discovered_models": list(extra.get("discovered_models") or []),
                "verified": bool(extra.get("verified")),
                "verified_at": extra.get("verified_at"),
                "manageable": key in MANAGEABLE_CLOUD,
                "last_error": extra.get("last_error"),
            }
        )
    return rows
