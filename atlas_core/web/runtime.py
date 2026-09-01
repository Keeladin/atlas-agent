from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, ScopeResolution

from .contracts import BrowserProvider, WebProvider, WebResponse
from .evidence import decode_text, evaluate_rendered_quality, evaluate_response_quality, media_type, provenance, translate_rendered_page, translate_response

READ_BYTES = 4 * 1024 * 1024
DOWNLOAD_BYTES = 20 * 1024 * 1024


def _canonical_url(value: Any) -> str:
    parsed = urlsplit(str(value or "").strip())
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").encode("idna").decode("ascii").casefold()
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("url must be an absolute HTTP(S) URL without credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("url has an invalid port") from exc
    if port and port not in {80, 443}:
        raise ValueError("url must use port 80 or 443")
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if port and port != (443 if scheme == "https" else 80):
        netloc += f":{port}"
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&;%:+,/?@!$'()*[]-._~")
    return urlunsplit((scheme, netloc, path, query, ""))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold()


def _receipt(response: WebResponse, operation: str) -> dict[str, Any]:
    source = provenance(response)
    return {
        "ok": 200 <= response.status < 300,
        "operation": operation,
        "provider": response.provider,
        "requested_url": source["requested_url"],
        "final_url": source["final_url"],
        "http_status": source["http_status"],
        "media_type": source["media_type"],
        "bytes": source["bytes"],
        "content_sha256": source["content_sha256"],
        "observed_at": source["retrieved_at"],
    }


class WebRuntime:
    """Stable web capability contracts above a replaceable, untrusted-data provider."""

    def __init__(self, registry: CapabilityRegistry, provider: WebProvider, download_root: Path, browser: BrowserProvider | None = None) -> None:
        self.registry = registry
        self.provider = provider
        self.browser = browser
        self.download_root = download_root
        self.download_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._register()

    def _register(self) -> None:
        text = {"type": "string", "minLength": 1}
        url = {"type": "string", "minLength": 8, "maxLength": 4096}
        search_schema = {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 400},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        }, "additionalProperties": False}
        url_schema = {"type": "object", "required": ["url"], "properties": {
            "url": url,
            "max_chars": {"type": "integer", "minimum": 1000, "maximum": 120000},
        }, "additionalProperties": False}
        fetch_schema = {"type": "object", "required": ["url"], "properties": {
            "url": url,
            "max_bytes": {"type": "integer", "minimum": 1024, "maximum": READ_BYTES},
        }, "additionalProperties": False}
        extract_schema = {"type": "object", "required": ["url"], "properties": {
            "url": url,
            "max_chars": {"type": "integer", "minimum": 1000, "maximum": 120000},
            "max_links": {"type": "integer", "minimum": 0, "maximum": 200},
        }, "additionalProperties": False}
        download_schema = {"type": "object", "required": ["url"], "properties": {
            "url": url,
            "filename": {"type": "string", "minLength": 1, "maxLength": 160},
        }, "additionalProperties": False}
        crawl_schema = {"type": "object", "required": ["url"], "properties": {
            "url": url,
            "max_pages": {"type": "integer", "minimum": 1, "maximum": 10},
            "max_chars_per_page": {"type": "integer", "minimum": 1000, "maximum": 40000},
        }, "additionalProperties": False}

        def reg(cid: str, description: str, operation: str, effect: str, schema: dict[str, Any], resolver, executor, *, available=None) -> None:
            self.registry.register(CapabilityRegistration(
                CapabilityDefinition(cid, description, operation, effect, schema, source="web", tags=("web", "internet", "evidence", "provenance")),
                resolver, executor, availability=available or self.provider.availability,
                metadata={"scope_hint": "web", "provider_neutral": True, "instruction_trust": "data_only"},
            ), replace=True)

        reg("web.search", "Search the current public web and return source URLs, snippets, and retrieval provenance.", "search", "none", search_schema,
            lambda p: ScopeResolution("web/search", {"query": str(p["query"]).strip(), "limit": int(p.get("limit") or 8)}, "Search the public web"), self._search,
            available=getattr(self.provider, "search_availability", self.provider.availability))
        reg("web.read", "Read a public web page as bounded human-readable text with source provenance.", "read", "none", url_schema,
            lambda p: self._url_scope(p, "read"), self._read)
        reg("web.fetch", "Fetch a bounded public HTTP(S) resource with response metadata and content evidence.", "fetch", "none", fetch_schema,
            lambda p: self._url_scope(p, "fetch"), self._fetch)
        reg("web.extract", "Extract text, title, and links from a public web resource with provenance.", "extract", "none", extract_schema,
            lambda p: self._url_scope(p, "extract"), self._extract)
        reg("web.download", "Download a bounded public web resource into Atlas managed intake without overwriting files.", "download", "reversible", download_schema,
            lambda p: self._url_scope(p, "download"), self._download)
        reg("web.crawl", "Crawl a small robots-aware set of same-origin public pages and return bounded evidence.", "crawl", "none", crawl_schema,
            lambda p: self._url_scope(p, "crawl"), self._crawl)
        browser_schema = {"type": "object", "required": ["url"], "properties": {
            "url": url,
            "max_chars": {"type": "integer", "minimum": 1000, "maximum": 120000},
            "timeout_ms": {"type": "integer", "minimum": 3000, "maximum": 30000},
            "settle_ms": {"type": "integer", "minimum": 0, "maximum": 5000},
        }, "additionalProperties": False}
        reg("web.browser.render", "Render a public JavaScript page read-only and return normalized visible-text evidence.", "render", "none", browser_schema,
            lambda p: self._url_scope(p, "render"), self._browser_render,
            available=(self.browser.availability if self.browser is not None else (lambda: (False, "browser_provider_not_configured"))))

    def _url_scope(self, payload: dict[str, Any], operation: str) -> ScopeResolution:
        normalized = dict(payload)
        normalized["url"] = _canonical_url(payload.get("url"))
        for key in ("max_chars", "max_links", "max_bytes", "max_pages", "max_chars_per_page"):
            if key in normalized:
                normalized[key] = int(normalized[key])
        if "filename" in normalized:
            normalized["filename"] = str(normalized["filename"]).strip()
        return ScopeResolution(f"web/{_host(normalized['url'])}", normalized, f"{operation.title()} {normalized['url']}")

    def _search(self, payload: dict[str, Any]) -> ActionResult:
        try:
            results = self.provider.search(payload["query"], limit=int(payload.get("limit") or 8))
            provider = str(results[0].get("provider") or self.provider.provider_id) if results else self.provider.provider_id
            output = {
                "schema": "atlas.web.search.v1", "instruction_trust": "data_only",
                "query": payload["query"], "results": results, "provider": provider,
            }
            return ActionResult(True, output, {"ok": True, "operation": "search", "provider": provider, "result_count": len(results)})
        except Exception as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "search", "provider": self.provider.provider_id}, error_code="web_search_failed", error=str(exc))

    def _response(self, payload: dict[str, Any], *, default_limit: int = READ_BYTES) -> WebResponse:
        response = self.provider.fetch(payload["url"], max_bytes=int(payload.get("max_bytes") or default_limit))
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"web provider returned HTTP {response.status}")
        return response

    def _read(self, payload: dict[str, Any]) -> ActionResult:
        try:
            response = self._response(payload)
            evidence = translate_response(response, max_chars=int(payload.get("max_chars") or 60000), max_links=0)
            quality = evaluate_response_quality(response, evidence)
            payload_out = evidence["payload"]
            output = {
                "schema": evidence["schema"], "evidence_type": evidence["evidence_type"],
                "instruction_trust": evidence["instruction_trust"], "translator": evidence["translator"],
                "url": evidence["provenance"]["final_url"], "title": evidence["title"],
                "text": payload_out.get("text", ""), "data": payload_out.get("data"),
                "body_omitted": payload_out.get("body_omitted", False),
                "text_truncated": evidence["text_truncated"], "provenance": evidence["provenance"],
                "content_quality": quality["content_quality"], "structural_metrics": quality["structural_metrics"],
                "provider": response.provider,
            }
            return ActionResult(True, output, _receipt(response, "read"))
        except Exception as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "read"}, error_code="web_read_failed", error=str(exc))

    def _fetch(self, payload: dict[str, Any]) -> ActionResult:
        try:
            response = self._response(payload)
            evidence = translate_response(response, max_chars=120000, max_links=0)
            output = {
                "schema": evidence["schema"], "evidence_type": evidence["evidence_type"],
                "instruction_trust": evidence["instruction_trust"], "translator": evidence["translator"],
                "url": response.final_url, "status": response.status,
                "headers": {key: value for key, value in response.headers.items() if key in {"content-type", "content-length", "etag", "last-modified", "cache-control"}},
                "payload": evidence["payload"], "text_truncated": evidence["text_truncated"],
                "provenance": evidence["provenance"], "provider": response.provider,
            }
            return ActionResult(True, output, _receipt(response, "fetch"))
        except Exception as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "fetch"}, error_code="web_fetch_failed", error=str(exc))

    def _extract(self, payload: dict[str, Any]) -> ActionResult:
        try:
            response = self._response(payload)
            evidence = translate_response(
                response, max_chars=int(payload.get("max_chars") or 60000),
                max_links=int(payload["max_links"]) if "max_links" in payload else 50,
            )
            output = {
                "schema": evidence["schema"], "evidence_type": evidence["evidence_type"],
                "instruction_trust": evidence["instruction_trust"], "translator": evidence["translator"],
                "url": evidence["provenance"]["final_url"], "title": evidence["title"],
                "text": evidence["payload"].get("text", ""), "data": evidence["payload"].get("data"),
                "body_omitted": evidence["payload"].get("body_omitted", False),
                "text_truncated": evidence["text_truncated"], "links": evidence["links"],
                "provenance": evidence["provenance"], "provider": response.provider,
            }
            return ActionResult(True, output, _receipt(response, "extract"))
        except Exception as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "extract"}, error_code="web_extract_failed", error=str(exc))

    def _browser_render(self, payload: dict[str, Any]) -> ActionResult:
        if self.browser is None:
            return ActionResult(False, receipt={"ok": False, "operation": "render"}, error_code="browser_provider_unavailable", error="rendered browser provider is not configured")
        try:
            page = self.browser.render(
                payload["url"], timeout_ms=int(payload.get("timeout_ms") or 15000),
                settle_ms=int(payload.get("settle_ms") or 1200), max_chars=int(payload.get("max_chars") or 60000),
            )
            evidence = translate_rendered_page(page, max_chars=int(payload.get("max_chars") or 60000))
            quality = evaluate_rendered_quality(page)
            output = {
                "schema": evidence["schema"], "evidence_type": evidence["evidence_type"],
                "instruction_trust": evidence["instruction_trust"], "translator": evidence["translator"],
                "url": evidence["provenance"]["final_url"], "title": evidence["title"],
                "text": evidence["payload"]["text"], "text_truncated": evidence["text_truncated"],
                "links": evidence["links"], "provenance": evidence["provenance"], "provider": page.provider,
                "content_quality": quality["content_quality"], "structural_metrics": quality["structural_metrics"],
            }
            receipt = {"ok": True, "operation": "render", "provider": page.provider, "requested_url": page.requested_url,
                       "final_url": page.final_url, "observed_at": page.rendered_at, "dom_sha256": page.dom_sha256,
                       "resource_count": page.resource_count, "resource_bytes": page.resource_bytes}
            return ActionResult(True, output, receipt)
        except Exception as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "render"}, error_code="web_browser_render_failed", error=str(exc))

    def _download(self, payload: dict[str, Any]) -> ActionResult:
        try:
            response = self._response(payload, default_limit=DOWNLOAD_BYTES)
            requested_name = str(payload.get("filename") or Path(urlsplit(response.final_url).path).name or "download")
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", requested_name).strip(".-")[:120] or "download"
            candidate = self.download_root / safe
            stem, suffix = candidate.stem, candidate.suffix
            counter = 1
            while candidate.exists():
                counter += 1
                candidate = self.download_root / f"{stem}-{counter}{suffix}"
            with candidate.open("xb") as handle:
                handle.write(response.body)
            output = {
                "saved_file": str(candidate), "filename": candidate.name, "url": response.final_url,
                "mimeType": media_type(response), "bytes": len(response.body),
                "content_sha256": hashlib.sha256(response.body).hexdigest(), "retrieved_at": response.fetched_at,
                "provider": response.provider,
            }
            return ActionResult(True, output, {**_receipt(response, "download"), "saved_file": str(candidate)})
        except Exception as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "download"}, error_code="web_download_failed", error=str(exc))

    def _crawl(self, payload: dict[str, Any]) -> ActionResult:
        start = payload["url"]
        limit = int(payload.get("max_pages") or 5)
        max_chars = int(payload.get("max_chars_per_page") or 20000)
        try:
            robots_url = urlunsplit((*urlsplit(start)[:2], "/robots.txt", "", ""))
            robots = RobotFileParser()
            try:
                robots_response = self.provider.fetch(robots_url, max_bytes=256 * 1024)
                robots.parse(decode_text(robots_response).splitlines())
            except Exception:
                robots.parse([])
            queue = deque([start]); seen: set[str] = set(); pages: list[dict[str, Any]] = []
            while queue and len(pages) < limit:
                url = queue.popleft()
                if url in seen or _host(url) != _host(start):
                    continue
                seen.add(url)
                if not robots.can_fetch("Atlas-Web", url):
                    pages.append({"url": url, "status": "robots_disallowed"})
                    continue
                response = self.provider.fetch(url, max_bytes=READ_BYTES)
                if response.status < 200 or response.status >= 300:
                    pages.append({"url": url, "status": response.status, "retrieved_at": response.fetched_at})
                    continue
                page = translate_response(response, max_chars=max_chars, max_links=100)
                links = page.pop("links")
                pages.append({
                    "schema": page["schema"], "evidence_type": page["evidence_type"],
                    "instruction_trust": page["instruction_trust"], "translator": page["translator"],
                    "url": page["provenance"]["final_url"], "title": page["title"],
                    "text": page["payload"].get("text", ""), "data": page["payload"].get("data"),
                    "body_omitted": page["payload"].get("body_omitted", False),
                    "text_truncated": page["text_truncated"], "provenance": page["provenance"],
                })
                for link in links:
                    try:
                        candidate = _canonical_url(link["url"])
                    except ValueError:
                        continue
                    if _host(candidate) == _host(start) and candidate not in seen:
                        queue.append(candidate)
            digest = hashlib.sha256(json.dumps(pages, sort_keys=True, default=str).encode()).hexdigest()
            return ActionResult(True, {"start_url": start, "pages": pages, "page_count": len(pages), "evidence_sha256": digest, "provider": self.provider.provider_id},
                                {"ok": True, "operation": "crawl", "provider": self.provider.provider_id, "page_count": len(pages), "evidence_sha256": digest})
        except Exception as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "crawl"}, error_code="web_crawl_failed", error=str(exc))
