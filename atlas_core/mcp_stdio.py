from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn, TypeVar

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_core.mcp_http import MCPClientProtocol


_T = TypeVar("_T")
_MAX_TOOL_PAGES = 100
_THREAD_TIMEOUT_GRACE_SEC = 1.0


class StdioMCPClient(MCPClientProtocol):
    """Synchronous MCP client for a directly spawned stdio server."""

    def __init__(
        self,
        command: str,
        *,
        args: Sequence[str] = (),
        cwd: str | Path | None = None,
        timeout_sec: float = 30.0,
        read_timeout_sec: float = 300.0,
    ) -> None:
        executable = str(command or "").strip()
        if not executable:
            raise ValueError("stdio MCP command must not be empty")
        resolved_timeout = float(timeout_sec)
        resolved_read = float(read_timeout_sec)
        _require_finite_timeout("timeout_sec", resolved_timeout)
        _require_finite_timeout("read_timeout_sec", resolved_read)
        self.command = executable
        self.args = tuple(str(item) for item in args)
        self.cwd = str(Path(cwd).expanduser()) if cwd else None
        self.timeout_sec = resolved_timeout
        self.read_timeout_sec = resolved_read
        self._call_timeout_sec = resolved_timeout + resolved_read

    def list_tools(self) -> list[dict[str, Any]]:
        return self._run(self._list_tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(name or "").strip()
        if not tool_name:
            raise ValueError("MCP tool name must not be empty")
        return self._run(self._call_tool, tool_name, dict(arguments))

    def _run(self, operation: Callable[..., Any], *args: Any) -> Any:
        def invoke() -> Any:
            try:
                return asyncio.run(operation(*args))
            except Exception as exc:
                _reraise_unwrapped(exc)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return invoke()
        return _run_in_thread(invoke, timeout_sec=self._call_timeout_sec + _THREAD_TIMEOUT_GRACE_SEC)

    async def _list_tools(self) -> list[dict[str, Any]]:
        async def operation(client: Client) -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            cursor: str | None = None
            for _ in range(_MAX_TOOL_PAGES):
                page = await client.list_tools(cursor=cursor)
                items.extend(_model_dict(tool) for tool in page.tools)
                if not page.next_cursor:
                    return items
                cursor = page.next_cursor
            raise RuntimeError("MCP tools/list pagination exceeded the adapter page limit")

        return await self._with_client(operation)

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async def operation(client: Client) -> dict[str, Any]:
            return _model_dict(await client.call_tool(name, arguments))

        return await self._with_client(operation)

    async def _with_client(self, operation: Callable[[Client], Any]) -> Any:
        params = StdioServerParameters(command=self.command, args=list(self.args), cwd=self.cwd)
        async with Client(stdio_client(params), read_timeout_seconds=self.read_timeout_sec) as client:
            return await operation(client)


def _require_finite_timeout(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite value > 0")


def _unwrap_exception(exc: BaseException) -> BaseException:
    current = exc
    while isinstance(current, BaseExceptionGroup) and len(current.exceptions) == 1:
        current = current.exceptions[0]
    return current


def _reraise_unwrapped(exc: BaseException) -> NoReturn:
    unwrapped = _unwrap_exception(exc)
    if unwrapped is exc:
        raise exc
    raise unwrapped from exc


def _model_dict(value: Any) -> dict[str, Any]:
    payload = value.model_dump(by_alias=True, mode="json", exclude_none=True)
    if not isinstance(payload, dict):
        raise TypeError("MCP SDK returned a non-object payload")
    return payload


def _run_in_thread(invoke: Callable[[], _T], *, timeout_sec: float) -> _T:
    box: dict[str, Any] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            box["result"] = invoke()
        except Exception as exc:
            box["error"] = _unwrap_exception(exc)
        finally:
            done.set()

    thread = threading.Thread(target=worker, name="atlas-mcp-stdio", daemon=True)
    thread.start()
    if not done.wait(timeout_sec):
        raise TimeoutError(f"MCP stdio call exceeded {timeout_sec:.3f}s")
    error = box.get("error")
    if error is not None:
        raise error
    if "result" not in box:
        raise RuntimeError("MCP stdio call did not return")
    return box["result"]
