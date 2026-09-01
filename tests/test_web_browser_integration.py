from __future__ import annotations

from datetime import datetime, timezone

import pytest

from atlas_core.web import WebResponse
from atlas_providers.web_browser import PlaywrightBrowserProvider


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_render_executes_javascript_but_only_returns_visible_data(monkeypatch):
    provider = PlaywrightBrowserProvider()
    available, reason = provider.availability()
    if not available:
        pytest.skip(reason)

    calls: list[tuple[str, str]] = []

    def fake_request(url: str, *, max_bytes: int, method: str = "GET", headers=None, body=None, **_kwargs):
        calls.append((method, url))
        if url.endswith("/api/live"):
            payload = b'{"value":42}'
            return WebResponse(url, url, 200, {"content-type": "application/json"}, payload, _now(), "fixture")
        html = b"""<!doctype html><html><head><title>Dynamic fixture</title></head><body>
<div id='app'></div>
<script>
fetch('/api/live').then(r => r.json()).then(d => { document.querySelector('#app').textContent = 'Live value: ' + d.value; });
fetch('/mutate', {method: 'POST', body: 'forbidden'}).catch(() => {});
window.__APP_STATE__ = {secret: 'must-not-cross'};
</script></body></html>"""
        return WebResponse(url, url, 200, {"content-type": "text/html"}, html, _now(), "fixture")

    monkeypatch.setattr("atlas_providers.web_browser._request", fake_request)
    page = provider.render(
        "https://fixture.example/", timeout_ms=15000, settle_ms=800, max_chars=5000,
    )

    assert page.title == "Dynamic fixture"
    assert "Live value: 42" in page.visible_text
    assert "must-not-cross" not in page.visible_text
    assert "window.__APP_STATE__" not in page.visible_text
    assert ("GET", "https://fixture.example/") in calls
    assert ("GET", "https://fixture.example/api/live") in calls
    assert not any(method == "POST" for method, _url in calls)
