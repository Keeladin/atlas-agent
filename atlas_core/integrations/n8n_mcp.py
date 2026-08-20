from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from atlas_core.mcp_http import StreamableHTTPMCPClient
from atlas_core.tools import MCPClientProtocol, MCPToolBridge, ToolGateway


DEFAULT_URL = "http://127.0.0.1:5678/mcp-server/http"
DEFAULT_SECRET_REF = "ATLAS_N8N_MCP_TOKEN"
DEFAULT_SERVER_NAME = "atlas-n8n"
DEFAULT_TOOL_PREFIX = "mcp.n8n"
DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_READ_TIMEOUT_SEC = 300.0

_BEARER_RE = re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+\S+")
logger = logging.getLogger(__name__)

ClientFactory = Callable[..., MCPClientProtocol]


@dataclass(frozen=True)
class N8NMCPConfig:
    enabled: bool = False
    server_name: str = DEFAULT_SERVER_NAME
    url: str = DEFAULT_URL
    tool_prefix: str = DEFAULT_TOOL_PREFIX
    timeout_sec: float = DEFAULT_TIMEOUT_SEC
    read_timeout_sec: float = DEFAULT_READ_TIMEOUT_SEC
    secret_ref: str = DEFAULT_SECRET_REF

    def __post_init__(self) -> None:
        _require_finite("timeout_sec", self.timeout_sec)
        _require_finite("read_timeout_sec", self.read_timeout_sec)
        if not str(self.secret_ref).strip():
            raise ValueError("secret_ref must not be empty")
        if not str(self.server_name).strip():
            raise ValueError("server_name must not be empty")
        if not str(self.tool_prefix).strip():
            raise ValueError("tool_prefix must not be empty")
        if self.enabled:
            parsed = urlparse(str(self.url).strip())
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("n8n MCP url must be an absolute http(s) URL")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> N8NMCPConfig:
        payload: Mapping[str, Any] = data
        nested = data.get("n8n_mcp")
        if isinstance(nested, Mapping):
            payload = nested
        secret_ref = str(payload.get("secret_ref") or DEFAULT_SECRET_REF).strip()
        return cls(
            enabled=bool(payload.get("enabled", False)),
            server_name=str(payload.get("server_name") or DEFAULT_SERVER_NAME).strip(),
            url=str(payload.get("url") or DEFAULT_URL).strip(),
            tool_prefix=str(payload.get("tool_prefix") or DEFAULT_TOOL_PREFIX).strip(),
            timeout_sec=float(payload.get("timeout_sec", DEFAULT_TIMEOUT_SEC)),
            read_timeout_sec=float(payload.get("read_timeout_sec", DEFAULT_READ_TIMEOUT_SEC)),
            secret_ref=secret_ref,
        )


@dataclass(frozen=True)
class N8NMCPStatus:
    configured: bool
    enabled: bool
    available: bool
    endpoint: str | None
    discovered_tool_count: int
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "available": self.available,
            "endpoint": self.endpoint,
            "discovered_tool_count": self.discovered_tool_count,
            "last_error": self.last_error,
        }


class N8NMCPProvider:
    """External n8n MCP tool provider.

    Owns connection configuration, discovery, and safe availability metadata.
    Registers discovered tools onto a caller-supplied ToolGateway through
    MCPToolBridge. Does not own Atlas tasks, authority, or workflow domain logic.
    """

    def __init__(
        self,
        config: N8NMCPConfig,
        *,
        configured: bool = True,
        environ: Mapping[str, str] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.config = config
        self._configured = bool(configured)
        self._environ = environ
        self._client_factory = client_factory or _default_client_factory
        self._tool_ids: tuple[str, ...] = ()
        self._status = _status_from_config(self._configured, config, available=False, count=0, error=None)

    @classmethod
    def unconfigured(cls) -> N8NMCPProvider:
        return cls(N8NMCPConfig(enabled=False), configured=False)

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> N8NMCPProvider:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("n8n MCP config must be a JSON object")
        return cls(N8NMCPConfig.from_mapping(data), environ=environ, client_factory=client_factory)

    @property
    def status(self) -> N8NMCPStatus:
        return self._status

    @property
    def tool_ids(self) -> tuple[str, ...]:
        return self._tool_ids

    def connect(self, gateway: ToolGateway) -> N8NMCPStatus:
        if self._status.available:
            return self._status
        if not self._configured:
            self._status = _status_from_config(False, self.config, available=False, count=0, error=None)
            return self._status
        if not self.config.enabled:
            self._status = _status_from_config(True, self.config, available=False, count=0, error=None)
            return self._status
        token = _resolve_secret(self.config.secret_ref, self._environ)
        if token is None:
            return self._unavailable(
                f"n8n MCP secret {self.config.secret_ref} is not configured",
                secret=None,
            )
        try:
            client = self._client_factory(
                self.config.url,
                headers=_bearer_headers(token),
                timeout_sec=self.config.timeout_sec,
                read_timeout_sec=self.config.read_timeout_sec,
            )
            ids = MCPToolBridge(client).register_discovered(
                gateway,
                prefix=self.config.tool_prefix,
                server_name=self.config.server_name,
                transport="streamable-http",
            )
        except Exception as exc:
            return self._unavailable(_redact(str(exc), token), secret=token)
        self._tool_ids = ids
        self._status = _status_from_config(
            True,
            self.config,
            available=True,
            count=len(ids),
            error=None,
            secret=token,
        )
        return self._status

    def _unavailable(self, message: str, *, secret: str | None) -> N8NMCPStatus:
        safe = _redact(message, secret)
        logger.warning("n8n MCP unavailable: %s", safe)
        self._tool_ids = ()
        self._status = _status_from_config(
            self._configured,
            self.config,
            available=False,
            count=0,
            error=safe,
            secret=secret,
        )
        return self._status

    def __repr__(self) -> str:
        return f"N8NMCPProvider({self.status.as_dict()!r})"


def _default_client_factory(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout_sec: float,
    read_timeout_sec: float,
) -> MCPClientProtocol:
    return StreamableHTTPMCPClient(
        url,
        headers=headers,
        timeout_sec=timeout_sec,
        read_timeout_sec=read_timeout_sec,
    )


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _resolve_secret(secret_ref: str, environ: Mapping[str, str] | None) -> str | None:
    source = os.environ if environ is None else environ
    value = source.get(secret_ref)
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite value > 0")


def _redact(text: str, secret: str | None) -> str:
    redacted = text
    if secret:
        redacted = redacted.replace(secret, "[redacted]")
    return _BEARER_RE.sub("[redacted-authorization]", redacted)


def _safe_endpoint(url: str, secret: str | None) -> str:
    parsed = urlparse(str(url).strip())
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    cleaned = urlunparse((parsed.scheme, host, parsed.path, "", "", ""))
    return _redact(cleaned, secret)


def _status_from_config(
    configured: bool,
    config: N8NMCPConfig,
    *,
    available: bool,
    count: int,
    error: str | None,
    secret: str | None = None,
) -> N8NMCPStatus:
    endpoint = _safe_endpoint(config.url, secret) if configured else None
    return N8NMCPStatus(
        configured=configured,
        enabled=bool(configured and config.enabled),
        available=available,
        endpoint=endpoint,
        discovered_tool_count=count,
        last_error=error,
    )
