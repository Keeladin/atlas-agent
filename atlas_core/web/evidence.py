from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from .contracts import WebResponse

WEB_EVIDENCE_SCHEMA = "atlas.web.evidence.v1"
SEARCH_EVIDENCE_SCHEMA = "atlas.web.search-result.v1"
INSTRUCTION_TRUST = "data_only"


class _DocumentParser(HTMLParser):
    """Deterministic HTML-to-evidence translator. Executable/presentation nodes never cross the boundary."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title: list[str] = []
        self.text: list[str] = []
        self.links: list[dict[str, str]] = []
        self._skip = 0
        self._in_title = False
        self._anchor: list[str] | None = None
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = True
        if tag == "a":
            self._anchor = []
            self._href = dict(attrs).get("href") or ""
        if tag in {"p", "div", "article", "section", "main", "header", "footer", "li", "br", "h1", "h2", "h3", "h4", "tr"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"} and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._anchor is not None:
            absolute = urljoin(self.base_url, self._href)
            if urlsplit(absolute).scheme in {"http", "https"}:
                self.links.append({"url": absolute, "text": " ".join("".join(self._anchor).split())})
            self._anchor = None
            self._href = ""

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
        if self._anchor is not None:
            self._anchor.append(data)
        self.text.append(data)


def media_type(response: WebResponse) -> str:
    return response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip().casefold()


def charset(response: WebResponse) -> str:
    match = re.search(r"charset=([^;\s]+)", response.headers.get("content-type", ""), flags=re.I)
    return match.group(1).strip("\"'") if match else "utf-8"


def decode_text(response: WebResponse) -> str:
    try:
        return response.body.decode(charset(response), errors="replace")
    except LookupError:
        return response.body.decode("utf-8", errors="replace")


def provenance(response: WebResponse) -> dict[str, Any]:
    return {
        "requested_url": response.requested_url,
        "final_url": response.final_url,
        "http_status": response.status,
        "media_type": media_type(response),
        "bytes": len(response.body),
        "content_sha256": hashlib.sha256(response.body).hexdigest(),
        "retrieved_at": response.fetched_at,
        "transport_provider": response.provider,
    }


def translate_response(response: WebResponse, *, max_chars: int, max_links: int = 0) -> dict[str, Any]:
    """Translate transport-native bytes into Atlas-native, non-executable evidence."""
    kind = media_type(response)
    decoded = decode_text(response)
    source = provenance(response)
    title = ""
    links: list[dict[str, str]] = []
    truncated = False

    if kind in {"text/html", "application/xhtml+xml"} or "<html" in decoded[:1000].casefold():
        parser = _DocumentParser(response.final_url)
        parser.feed(decoded)
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in "".join(parser.text).splitlines() if line.strip()))
        title = " ".join("".join(parser.title).split())
        links = parser.links[:max_links]
        payload: dict[str, Any] = {"text": text[:max_chars]}
        truncated = len(text) > max_chars
        evidence_type = "web_page"
        translator = "html-text-v1"
    elif kind == "application/json" or kind.endswith("+json"):
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise ValueError("web JSON response could not be parsed deterministically") from exc
        serialized = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(serialized) > max_chars:
            raise ValueError(f"normalized JSON evidence exceeds {max_chars} character limit")
        payload = {"data": parsed}
        evidence_type = "structured_data"
        translator = "json-v1"
    elif kind.startswith("text/") or kind in {"application/xml", "application/yaml", "text/yaml"} or kind.endswith("+xml"):
        payload = {"text": decoded[:max_chars]}
        truncated = len(decoded) > max_chars
        evidence_type = "text_document"
        translator = "plain-text-v1"
    else:
        payload = {"body_omitted": True}
        evidence_type = "binary_resource"
        translator = "metadata-only-v1"

    return {
        "schema": WEB_EVIDENCE_SCHEMA,
        "evidence_type": evidence_type,
        "instruction_trust": INSTRUCTION_TRUST,
        "translator": translator,
        "title": title,
        "payload": payload,
        "text_truncated": truncated,
        "links": links,
        "provenance": source,
    }


def normalize_search_result(*, provider: str, title: str, url: str, snippet: str,
                            source_timestamp: Any, retrieved_at: str) -> dict[str, object]:
    """Translate provider-native search rows into one stable Atlas evidence contract."""
    return {
        "schema": SEARCH_EVIDENCE_SCHEMA,
        "evidence_type": "search_result",
        "instruction_trust": INSTRUCTION_TRUST,
        "translator": "search-result-v1",
        "title": title,
        "url": url,
        "snippet": snippet[:8000],
        "provider": provider,
        "source_timestamp": source_timestamp,
        "retrieved_at": retrieved_at,
    }


def evaluate_response_quality(response: WebResponse, evidence: dict[str, Any]) -> dict[str, Any]:
    """Report deterministic structural quality signals without granting any authority."""
    kind = media_type(response)
    if kind not in {"text/html", "application/xhtml+xml"}:
        return {
            "content_quality": {"status": "usable", "signals": []},
            "structural_metrics": {"raw_bytes": len(response.body), "visible_text_bytes": 0, "script_bytes": 0,
                                   "script_to_visible_text_ratio": 0.0, "has_noscript_alert": False},
        }

    raw = decode_text(response)
    visible = str((evidence.get("payload") or {}).get("text") or "")
    script_bytes = sum(len(match.group(0).encode("utf-8", errors="replace")) for match in re.finditer(
        r"<script\b[^>]*>.*?</script\s*>", raw, flags=re.I | re.S,
    ))
    visible_bytes = len(visible.encode("utf-8", errors="replace"))
    ratio = script_bytes / max(visible_bytes, 1)
    lower = raw.casefold()
    visible_lower = visible.casefold()
    noscript_alert = bool(re.search(
        r"<noscript\b[^>]*>.*?(enable javascript|javascript[^<]{0,80}(required|enabled)|supported browser).*?</noscript\s*>",
        raw, flags=re.I | re.S,
    ))
    visible_challenge = any(token in visible_lower for token in (
        "verify you are human", "captcha", "access denied", "checking your browser", "security challenge",
    ))
    embedded_challenge = any(token in lower for token in ("cf-chl-", "challenge-platform")) and visible_bytes < 1000
    challenge = visible_challenge or embedded_challenge
    signals: list[str] = []
    status = "usable"
    if challenge:
        status = "blocked"
        signals.append("challenge_detected")
    elif visible_bytes < 20 and script_bytes < 1000:
        status = "empty"
        signals.append("low_visible_text")
    elif noscript_alert or (visible_bytes < 1500 and script_bytes > max(5000, visible_bytes * 25)):
        status = "dynamic_suspected"
        if noscript_alert:
            signals.append("javascript_required_message")
        if script_bytes > max(5000, visible_bytes * 25):
            signals.append("script_dominant")
        if visible_bytes < 300:
            signals.append("low_visible_text")
    if evidence.get("text_truncated"):
        signals.append("truncated")

    return {
        "content_quality": {"status": status, "signals": signals},
        "structural_metrics": {
            "raw_bytes": len(response.body),
            "visible_text_bytes": visible_bytes,
            "script_bytes": script_bytes,
            "script_to_visible_text_ratio": round(ratio, 4),
            "has_noscript_alert": noscript_alert,
        },
    }


def translate_rendered_page(page, *, max_chars: int) -> dict[str, Any]:
    """Translate rendered browser state into Atlas-native visible-text evidence."""
    text = str(page.visible_text or "")[:max_chars]
    return {
        "schema": WEB_EVIDENCE_SCHEMA,
        "evidence_type": "rendered_web_page",
        "instruction_trust": INSTRUCTION_TRUST,
        "translator": "rendered-visible-text-v1",
        "title": str(page.title or ""),
        "payload": {"text": text},
        "text_truncated": len(str(page.visible_text or "")) > max_chars,
        "links": list(page.links),
        "provenance": {
            "requested_url": page.requested_url,
            "final_url": page.final_url,
            "retrieved_at": page.rendered_at,
            "transport_provider": page.provider,
            "dom_sha256": page.dom_sha256,
            "resource_count": page.resource_count,
            "resource_bytes": page.resource_bytes,
        },
    }


def evaluate_rendered_quality(page) -> dict[str, Any]:
    """Classify rendered visible text without attempting to bypass access controls."""
    text = str(page.visible_text or "")
    lower = text.casefold()
    signals: list[str] = []
    status = "usable"
    if not text.strip():
        status = "empty"
        signals.append("low_visible_text")
    elif any(token in lower for token in (
        "verify you are human", "captcha", "access denied", "checking your browser",
        "unusual traffic", "security challenge",
    )):
        status = "blocked"
        signals.append("challenge_detected")
    elif len(text.encode("utf-8", errors="replace")) < 40:
        status = "empty"
        signals.append("low_visible_text")
    return {
        "content_quality": {"status": status, "signals": signals},
        "structural_metrics": {
            "visible_text_bytes": len(text.encode("utf-8", errors="replace")),
            "resource_count": int(page.resource_count),
            "resource_bytes": int(page.resource_bytes),
        },
    }
