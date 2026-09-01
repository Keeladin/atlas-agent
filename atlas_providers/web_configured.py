from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from atlas_core.secrets import CredentialStore
from atlas_core.web import WebProviderSettings, WebProviderSettingsStore
from atlas_core.web.evidence import normalize_search_result

from .web_standard import StandardWebProvider, _request


class ConfiguredWebProvider:
    """Routes stable web contracts to enabled providers without moving authority."""

    provider_id = "web-provider-router"

    def __init__(self, settings: WebProviderSettingsStore, secrets: CredentialStore) -> None:
        self.settings = settings
        self.secrets = secrets
        self.direct = StandardWebProvider()

    def public_state(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.public() for item in self.settings.all())

    def availability(self) -> tuple[bool, str]:
        return self.direct.availability()

    def search_availability(self) -> tuple[bool, str]:
        enabled = [row for row in self.settings.all() if row.enabled and row.credential_ref]
        return (True, "available") if enabled else (False, "search_provider_not_configured")

    def fetch(self, url: str, *, max_bytes: int):
        return self.direct.fetch(url, max_bytes=max_bytes)

    def search(self, query: str, *, limit: int) -> list[dict[str, object]]:
        enabled = sorted((row for row in self.settings.all() if row.enabled), key=lambda row: row.priority, reverse=True)
        if not enabled:
            raise RuntimeError("no web search provider is configured")
        errors = []
        for row in enabled:
            try:
                results = [item for item in self._search(row, query, limit) if str(item.get("url") or "").strip()]
                if results:
                    return results
                errors.append(f"{row.key}: no usable results")
            except Exception as exc:
                errors.append(f"{row.key}: {exc}")
        raise RuntimeError("all enabled web search providers failed: " + " | ".join(errors))

    def verify(self, key: str) -> dict[str, Any]:
        row = self.settings.get(key)
        results = self._search(row, "Atlas web provider verification", 1)
        return {"ok": bool(results), "provider": row.key, "kind": row.kind, "result_count": len(results)}

    def _search(self, row: WebProviderSettings, query: str, limit: int) -> list[dict[str, object]]:
        if not row.credential_ref:
            raise RuntimeError("web provider credential is not configured")
        secret = self.secrets.retrieve(row.credential_ref)
        key = str(secret.get("api_key") or "").strip()
        if not key:
            raise RuntimeError("web provider credential has no api_key")
        if row.kind == "jina":
            response = _request(
                "https://s.jina.ai/?q=" + quote_plus(query), max_bytes=4 * 1024 * 1024,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
            payload = _json_response(response.status, response.body, row.key)
            raw = payload.get("data") if isinstance(payload, dict) else None
            rows = raw if isinstance(raw, list) else []
            return [_normalized(row.key, item, snippet_keys=("description", "content")) for item in rows[:limit] if isinstance(item, dict)]
        if row.kind == "brave":
            response = _request(
                "https://api.search.brave.com/res/v1/web/search?q=" + quote_plus(query) + f"&count={limit}",
                max_bytes=2 * 1024 * 1024,
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
            )
            payload = _json_response(response.status, response.body, row.key)
            web = payload.get("web") if isinstance(payload, dict) else None
            rows = web.get("results") if isinstance(web, dict) else None
            return [_normalized(row.key, item, snippet_keys=("description",)) for item in (rows or [])[:limit] if isinstance(item, dict)]
        if row.kind == "tavily":
            body = json.dumps({"query": query, "max_results": limit, "search_depth": "basic", "include_answer": False}).encode()
            response = _request(
                "https://api.tavily.com/search", max_bytes=2 * 1024 * 1024, method="POST", body=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"},
            )
            payload = _json_response(response.status, response.body, row.key)
            rows = payload.get("results") if isinstance(payload, dict) else None
            return [_normalized(row.key, item, snippet_keys=("content",)) for item in (rows or [])[:limit] if isinstance(item, dict)]
        if row.kind == "serper":
            body = json.dumps({"q": query, "num": limit}).encode()
            response = _request(
                "https://google.serper.dev/search", max_bytes=2 * 1024 * 1024, method="POST", body=body,
                headers={"X-API-KEY": key, "Content-Type": "application/json", "Accept": "application/json"},
            )
            payload = _json_response(response.status, response.body, row.key)
            rows = payload.get("organic") if isinstance(payload, dict) else None
            return [_normalized(row.key, item, url_keys=("link", "url"), snippet_keys=("snippet",)) for item in (rows or [])[:limit] if isinstance(item, dict)]
        raise RuntimeError(f"unsupported web provider kind: {row.kind}")


def _json_response(status: int, body: bytes, provider: str) -> Any:
    if status < 200 or status >= 300:
        raise RuntimeError(f"{provider} returned HTTP {status}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider} returned invalid JSON") from exc


def _normalized(provider: str, item: dict[str, Any], *, url_keys=("url",), snippet_keys=("description", "snippet", "content")) -> dict[str, object]:
    url = next((str(item.get(key) or "").strip() for key in url_keys if str(item.get(key) or "").strip()), "")
    title = str(item.get("title") or item.get("name") or url).strip()
    snippet = next((str(item.get(key) or "").strip() for key in snippet_keys if str(item.get(key) or "").strip()), "")
    retrieved = item.get("publishedTime") or item.get("published_date") or item.get("page_age")
    return normalize_search_result(
        provider=provider, title=title, url=url, snippet=snippet, source_timestamp=retrieved,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
