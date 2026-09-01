from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from atlas_core.web import WebResponse

USER_AGENT = "Atlas-Web/1.0 (+provider-neutral governed web capability)"
_SENSITIVE_REDIRECT_HEADERS = {
    "authorization", "proxy-authorization", "cookie", "cookie2",
    "origin", "referer",
}
_CONTROLLED_REQUEST_HEADERS = {"host", "connection", "content-length", "transfer-encoding", "accept-encoding"}


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError(f"web host could not be resolved: {host}") from exc
    addresses: list[str] = []
    for row in rows:
        value = row[4][0]
        address = ipaddress.ip_address(value)
        if not address.is_global:
            raise PermissionError(f"web target resolves to a non-public address: {host}")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise RuntimeError(f"web host has no usable address: {host}")
    return tuple(addresses)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, port: int, *, timeout: float) -> None:
        super().__init__(address, port, timeout=timeout, context=ssl.create_default_context())
        self._atlas_hostname = hostname

    def connect(self) -> None:
        http.client.HTTPConnection.connect(self)
        assert self.sock is not None
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._atlas_hostname)


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("web URL must be an absolute HTTP(S) URL without credentials")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("web URL has an invalid port") from exc
    if port not in {80, 443}:
        raise ValueError("web URL must use port 80 or 443")
    return scheme, host, port


def _canonical_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    scheme, raw_host, port = _origin(str(value or "").strip())
    try:
        address = ipaddress.ip_address(raw_host)
        host = str(address)
    except ValueError:
        try:
            host = raw_host.encode("idna").decode("ascii").casefold()
        except UnicodeError as exc:
            raise ValueError("web URL has an invalid hostname") from exc
    host_part = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    netloc = host_part if port == default_port else f"{host_part}:{port}"
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&;%:+,/?@!$'()*[]-._~")
    return urlunsplit((scheme, netloc, path, query, ""))


def _caller_headers(headers: dict[str, str] | None) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if str(key).casefold() not in _CONTROLLED_REQUEST_HEADERS
    }


def _is_sensitive_header(name: str) -> bool:
    normalized = name.casefold().replace("_", "-")
    return (
        normalized in _SENSITIVE_REDIRECT_HEADERS
        or "api-key" in normalized
        or normalized.endswith("-token")
        or normalized in {"token", "apikey"}
    )


def _cross_origin_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if not _is_sensitive_header(key)}


def _without_entity_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: value for key, value in headers.items()
        if key.casefold() not in {"content-type", "content-encoding", "content-language", "content-location"}
    }


def _request(url: str, *, max_bytes: int, redirects: int = 4, method: str = "GET",
             headers: dict[str, str] | None = None, body: bytes | None = None) -> WebResponse:
    requested = _canonical_url(url)
    current = requested
    active_headers = _caller_headers(headers)
    active_method = str(method or "GET").upper()
    active_body = body
    for _ in range(redirects + 1):
        scheme, host, port = _origin(current)
        address = _public_addresses(host, port)[0]
        parsed = urlsplit(current)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection: http.client.HTTPConnection
        if scheme == "https":
            connection = _PinnedHTTPSConnection(host, address, port, timeout=15)
        else:
            connection = http.client.HTTPConnection(address, port, timeout=15)
        host_name = f"[{host}]" if ":" in host else host
        default_port = 443 if scheme == "https" else 80
        host_header = host_name if port == default_port else f"{host_name}:{port}"
        try:
            request_headers = {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json,text/plain,application/pdf,*/*;q=0.2",
                **active_headers,
                "Host": host_header,
                "Accept-Encoding": "identity",
                "Connection": "close",
            }
            connection.request(active_method, target, body=active_body, headers=request_headers)
            response = connection.getresponse()
            response_headers = {key.casefold(): value for key, value in response.getheaders()}
            if response.status in {301, 302, 303, 307, 308}:
                location = response_headers.get("location")
                response.read(1024)
                if not location:
                    raise RuntimeError("web redirect had no location")
                redirected = _canonical_url(urljoin(current, location))
                next_scheme, next_host, next_port = _origin(redirected)
                if scheme == "https" and next_scheme != "https":
                    raise PermissionError("HTTPS web requests may not redirect to plaintext HTTP")
                if (scheme, host, port) != (next_scheme, next_host, next_port):
                    active_headers = _cross_origin_headers(active_headers)
                if response.status == 303 or (response.status in {301, 302} and active_method not in {"GET", "HEAD"}):
                    active_method = "GET"
                    active_body = None
                    active_headers = _without_entity_headers(active_headers)
                current = redirected
                continue
            declared = response_headers.get("content-length")
            if declared and int(declared) > max_bytes:
                raise ValueError(f"web response exceeds {max_bytes} byte limit")
            response_body = response.read(max_bytes + 1)
            if len(response_body) > max_bytes:
                raise ValueError(f"web response exceeds {max_bytes} byte limit")
            return WebResponse(requested, current, response.status, response_headers, response_body, _iso(), "standard-http")
        finally:
            connection.close()
    raise RuntimeError("web redirect limit exceeded")


class StandardWebProvider:
    """Pinned direct HTTP provider for public web fetches."""

    provider_id = "standard"

    def availability(self) -> tuple[bool, str]:
        return True, "available"

    def search_availability(self) -> tuple[bool, str]:
        return False, "search_provider_not_configured"

    def fetch(self, url: str, *, max_bytes: int) -> WebResponse:
        return _request(url, max_bytes=max_bytes)

    def search(self, query: str, *, limit: int) -> list[dict[str, object]]:
        raise RuntimeError("web search provider is not configured")
