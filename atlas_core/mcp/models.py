from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MCPServer:
    server_id: str
    display_name: str
    kind: str
    transport: str
    url: str | None
    command: str | None
    args: tuple[str, ...]
    cwd: str | None
    enabled: bool
    credential_ref: str | None
    timeout_sec: float
    read_timeout_sec: float
    last_error: str | None
    last_discovered_at: str | None
    updated_at: str

    def public(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "transport": self.transport,
            "url": self.url,
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd,
            "enabled": self.enabled,
            "credential_configured": bool(self.credential_ref),
            "timeout_sec": self.timeout_sec,
            "read_timeout_sec": self.read_timeout_sec,
            "last_error": self.last_error,
            "last_discovered_at": self.last_discovered_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MCPTool:
    server_id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]
