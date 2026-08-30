from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_core.mcp_http import MCPClientProtocol


_MAX_TOOL_PAGES = 100


class StdioMCPClient(MCPClientProtocol):
    """Synchronous facade over one persistent stdio MCP session."""

    def __init__(self, command: str, *, args: Sequence[str] = (), cwd: str | Path | None = None,
                 timeout_sec: float = 30.0, read_timeout_sec: float = 300.0) -> None:
        executable = str(command or "").strip()
        if not executable: raise ValueError("stdio MCP command must not be empty")
        self.command=executable;self.args=tuple(str(item) for item in args);self.cwd=str(Path(cwd).expanduser()) if cwd else None
        self.timeout_sec=float(timeout_sec);self.read_timeout_sec=float(read_timeout_sec)
        _require_finite_timeout("timeout_sec",self.timeout_sec);_require_finite_timeout("read_timeout_sec",self.read_timeout_sec)
        self._call_timeout_sec=self.timeout_sec+self.read_timeout_sec
        self._guard=threading.Lock();self._thread=None;self._loop=None;self._client=None;self._stop=None;self._ready=threading.Event();self._start_error=None

    def list_tools(self) -> list[dict[str, Any]]:
        async def op(client: Client):
            items=[];cursor=None
            for _ in range(_MAX_TOOL_PAGES):
                page=await client.list_tools(cursor=cursor);items.extend(_model_dict(tool) for tool in page.tools)
                if not page.next_cursor:return items
                cursor=page.next_cursor
            raise RuntimeError("MCP tools/list pagination exceeded the adapter page limit")
        return self._submit(op)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_name=str(name or "").strip()
        if not tool_name:raise ValueError("MCP tool name must not be empty")
        async def op(client: Client):return _model_dict(await client.call_tool(tool_name,dict(arguments)))
        return self._submit(op)

    def _submit(self, operation: Callable[[Client], Any]) -> Any:
        self._ensure_session()
        assert self._loop is not None and self._client is not None
        async def run():return await operation(self._client)
        future=asyncio.run_coroutine_threadsafe(run(),self._loop)
        try:return future.result(timeout=self._call_timeout_sec)
        except TimeoutError:
            future.cancel();self.close();raise TimeoutError(f"MCP stdio call exceeded {self._call_timeout_sec:.3f}s")
        except Exception as exc:
            self.close();_reraise_unwrapped(exc)

    def _ensure_session(self) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive() and self._client is not None:return
            self._ready=threading.Event();self._start_error=None
            self._thread=threading.Thread(target=self._thread_main,name="atlas-mcp-stdio",daemon=True);self._thread.start()
        if not self._ready.wait(self.timeout_sec):
            self.close();raise TimeoutError(f"MCP stdio session start exceeded {self.timeout_sec:.3f}s")
        if self._start_error is not None:
            error=self._start_error;self.close();raise error

    def _thread_main(self) -> None:
        try:asyncio.run(self._session_main())
        except Exception as exc:
            self._start_error=_unwrap_exception(exc);self._ready.set()

    async def _session_main(self) -> None:
        params=StdioServerParameters(command=self.command,args=list(self.args),cwd=self.cwd)
        stop=asyncio.Event();self._stop=stop;self._loop=asyncio.get_running_loop()
        async with Client(stdio_client(params),read_timeout_seconds=self.read_timeout_sec) as client:
            self._client=client;self._ready.set();await stop.wait()
        self._client=None

    def close(self) -> None:
        with self._guard:
            loop=self._loop;stop=self._stop;thread=self._thread
            self._loop=None;self._stop=None;self._client=None;self._thread=None
        if loop is not None and stop is not None and loop.is_running():loop.call_soon_threadsafe(stop.set)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():thread.join(timeout=2.0)

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
