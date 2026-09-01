from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from atlas_core.web import RenderedPage

from .web_standard import _request

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

MAX_RESOURCE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_RESOURCES = 180
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


def _browser_library_path() -> str | None:
    configured = os.environ.get("ATLAS_BROWSER_LIBRARY_PATH", "").strip()
    if configured:
        return configured
    candidate = Path(__file__).resolve().parents[1] / ".browser-libs" / "root" / "usr" / "lib" / "x86_64-linux-gnu"
    return str(candidate) if candidate.is_dir() else None


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlaywrightBrowserProvider:
    """Read-only JS renderer whose network is supplied by Atlas controlled HTTP transport."""

    provider_id = "playwright-chromium"

    def availability(self) -> tuple[bool, str]:
        try:
            import playwright
        except Exception:
            return False, "playwright_not_installed"
        local = Path(playwright.__file__).resolve().parent / "driver" / "package" / ".local-browsers"
        executables = list(local.glob("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))
        if not executables:
            executables = list(local.glob("chromium-*/chrome-linux*/chrome"))
        executable = next((item for item in executables if item.is_file()), None)
        if executable is None:
            return False, "chromium_not_installed"
        env = dict(os.environ)
        local_libs = _browser_library_path()
        if local_libs:
            env["LD_LIBRARY_PATH"] = local_libs + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        check = subprocess.run(["ldd", str(executable)], capture_output=True, text=True, timeout=5, env=env)
        if "not found" in (check.stdout + check.stderr):
            return False, "browser_system_dependencies_missing"
        return True, "available"

    def render(self, url: str, *, timeout_ms: int, settle_ms: int, max_chars: int) -> RenderedPage:
        from playwright.sync_api import sync_playwright

        counters = {"resources": 0, "bytes": 0}
        with sync_playwright() as runtime:
            launch_env = dict(os.environ)
            local_libs = _browser_library_path()
            if local_libs:
                launch_env["LD_LIBRARY_PATH"] = local_libs + (":" + launch_env["LD_LIBRARY_PATH"] if launch_env.get("LD_LIBRARY_PATH") else "")
            browser = runtime.chromium.launch(
                headless=True,
                env=launch_env,
                proxy={"server": "http://127.0.0.1:9"},
                args=[
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--no-first-run",
                ],
            )
            context = browser.new_context(java_script_enabled=True, service_workers="block", accept_downloads=False)
            allowed_document_hosts = {(urlsplit(url).hostname or "").casefold()}

            def route_handler(route, request) -> None:
                if request.method.upper() not in {"GET", "HEAD"}:
                    route.abort()
                    return
                if request.resource_type in _BLOCKED_RESOURCE_TYPES:
                    route.abort()
                    return
                request_host = (urlsplit(request.url).hostname or "").casefold()
                if request.resource_type == "document" and request_host not in allowed_document_hosts:
                    route.abort()
                    return
                if counters["resources"] >= MAX_RESOURCES or counters["bytes"] >= MAX_TOTAL_BYTES:
                    route.abort()
                    return
                try:
                    response = _request(
                        request.url,
                        max_bytes=min(MAX_RESOURCE_BYTES, MAX_TOTAL_BYTES - counters["bytes"]),
                        method=request.method,
                        headers=dict(request.headers),
                    )
                    counters["resources"] += 1
                    counters["bytes"] += len(response.body)
                    if response.final_url.rstrip("/") != request.url.rstrip("/"):
                        final_host = (urlsplit(response.final_url).hostname or "").casefold()
                        if request.resource_type == "document" and final_host:
                            allowed_document_hosts.add(final_host)
                        route.fulfill(status=302, headers={"location": response.final_url}, body=b"")
                        return
                    route.fulfill(status=response.status, headers=response.headers, body=response.body)
                except Exception:
                    route.abort()

            context.route("**/*", route_handler)
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(settle_ms)
                title = page.title()
                final_url = page.url
                visible_text = page.evaluate(r"""() => {
                    const skip = new Set(['SCRIPT','STYLE','NOSCRIPT','SVG','TEMPLATE']);
                    const root = document.body;
                    if (!root) return '';
                    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                    const parts = [];
                    let node;
                    while ((node = walker.nextNode())) {
                        const el = node.parentElement;
                        if (!el || skip.has(el.tagName) || el.closest('script,style,noscript,svg,template')) continue;
                        const style = getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') continue;
                        const text = (node.nodeValue || '').replace(/\s+/g, ' ').trim();
                        if (text) parts.push(text);
                    }
                    return parts.join('\n');
                }""") or ""
                links_raw: list[dict[str, Any]] = page.locator("a[href]").evaluate_all(
                    "els => els.slice(0, 200).map(a => ({url: a.href, text: (a.innerText || a.textContent || '').trim()}))"
                )
                dom = page.content()
                links = tuple(
                    {"url": str(item.get("url") or ""), "text": str(item.get("text") or "")[:1000]}
                    for item in links_raw
                    if str(item.get("url") or "").startswith(("http://", "https://"))
                )
                return RenderedPage(
                    requested_url=url,
                    final_url=final_url,
                    title=title[:2000],
                    visible_text=visible_text[:max_chars],
                    links=links,
                    rendered_at=_iso(),
                    provider=self.provider_id,
                    dom_sha256=hashlib.sha256(dom.encode("utf-8", errors="replace")).hexdigest(),
                    resource_count=counters["resources"],
                    resource_bytes=counters["bytes"],
                )
            finally:
                context.close()
                browser.close()
