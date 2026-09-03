from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions


_LOCATION_PROVIDERS = {"gps", "network", "passive"}
_LOCATION_REQUESTS = {"once", "last"}
_PHONE_NUMBER = re.compile(r"^\+?[0-9]{6,20}$")
_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_SMS_CHARS = 5000


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_number(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("recipient number is required")
    normalized = re.sub(r"[\s()\-]", "", raw)
    if not _PHONE_NUMBER.fullmatch(normalized):
        raise ValueError("recipient number must contain only an optional leading + and 6-20 digits")
    return normalized


def _message(value: Any) -> str:
    text = str(value or "")
    if not text.strip():
        raise ValueError("SMS message must not be empty")
    if "\x00" in text:
        raise ValueError("SMS message must not contain NUL characters")
    if len(text) > _MAX_SMS_CHARS:
        raise ValueError(f"SMS message exceeds {_MAX_SMS_CHARS} character limit")
    return text


def _tool_result(payload: dict[str, Any]) -> types.CallToolResult:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return types.CallToolResult(
        content=[types.TextContent(text=rendered)],
        structuredContent=payload,
        isError=False,
    )


def _tool_error(message: str) -> types.CallToolResult:
    text = str(message)[:4000]
    return types.CallToolResult(
        content=[types.TextContent(text=text)],
        structuredContent={"error": text},
        isError=True,
    )


class AndroidPhoneProvider:
    """Narrow Termux:API bridge for location, telephony inspection and SIM SMS."""

    def __init__(
        self,
        *,
        location_command: str = "termux-location",
        sms_command: str = "termux-sms-send",
        deviceinfo_command: str = "termux-telephony-deviceinfo",
        cellinfo_command: str = "termux-telephony-cellinfo",
        execution_timeout_sec: float = 45.0,
    ) -> None:
        self.location_command = location_command
        self.sms_command = sms_command
        self.deviceinfo_command = deviceinfo_command
        self.cellinfo_command = cellinfo_command
        self.execution_timeout_sec = float(execution_timeout_sec)
        if self.execution_timeout_sec <= 0:
            raise ValueError("execution timeout must be greater than zero")

    def _available(self, command: str) -> bool:
        return shutil.which(command) is not None

    async def list_tools(self) -> list[types.Tool]:
        tools: list[types.Tool] = []
        if self._available(self.location_command):
            tools.append(types.Tool(
                name="phone_location_get",
                description=(
                    "Get the Android phone's current physical location through Termux:API for questions such as 'where am I'. "
                    "This establishes the owner's position for objectives that depend on proximity, nearby places, navigation, "
                    "distance, or other location-aware context. Defaults to a fresh GPS fix "
                    "(provider=gps, request=once); request=last explicitly returns cached location."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string", "enum": sorted(_LOCATION_PROVIDERS), "default": "gps"},
                        "request": {"type": "string", "enum": sorted(_LOCATION_REQUESTS), "default": "once"},
                    },
                    "additionalProperties": False,
                },
                annotations=types.ToolAnnotations(
                    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
                ),
            ))
        if self._available(self.deviceinfo_command) or self._available(self.cellinfo_command):
            tools.append(types.Tool(
                name="phone_telephony_inspect",
                description="Inspect SIM, carrier, radio and available cellular information exposed by Termux:API.",
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
                annotations=types.ToolAnnotations(
                    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
                ),
            ))
        if self._available(self.sms_command):
            tools.append(types.Tool(
                name="phone_sms_send",
                description="Send one SMS through the Android device SIM using Termux:API.",
                inputSchema={
                    "type": "object",
                    "required": ["number", "message"],
                    "properties": {
                        "number": {"type": "string", "minLength": 6, "maxLength": 32},
                        "message": {"type": "string", "minLength": 1, "maxLength": _MAX_SMS_CHARS},
                    },
                    "additionalProperties": False,
                },
                annotations=types.ToolAnnotations(
                    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True,
                ),
            ))
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        try:
            if name == "phone_location_get":
                return await self._location(arguments)
            if name == "phone_telephony_inspect":
                return await self._telephony()
            if name == "phone_sms_send":
                return await self._sms(arguments)
            return _tool_error(f"unknown Android phone tool: {name}")
        except Exception as exc:
            return _tool_error(str(exc))

    async def _location(self, arguments: dict[str, Any]) -> types.CallToolResult:
        if not self._available(self.location_command):
            raise RuntimeError("termux-location is not installed")
        provider = str(arguments.get("provider") or "gps").strip().casefold()
        request = str(arguments.get("request") or "once").strip().casefold()
        if provider not in _LOCATION_PROVIDERS:
            raise ValueError(f"unsupported location provider: {provider}")
        if request not in _LOCATION_REQUESTS:
            raise ValueError(f"unsupported location request: {request}")
        location = await self._run_json(self.location_command, "-p", provider, "-r", request)
        payload = {
            "provider_requested": provider,
            "request": request,
            "fresh_fix_requested": request == "once",
            "observed_at": _iso(),
            "location": location,
        }
        return _tool_result(payload)

    async def _telephony(self) -> types.CallToolResult:
        payload: dict[str, Any] = {"observed_at": _iso()}
        successes = 0
        if self._available(self.deviceinfo_command):
            try:
                payload["device"] = await self._run_json(self.deviceinfo_command)
                successes += 1
            except Exception as exc:
                payload["device_error"] = str(exc)[:1000]
        if self._available(self.cellinfo_command):
            try:
                payload["cells"] = await self._run_json(self.cellinfo_command)
                successes += 1
            except Exception as exc:
                payload["cell_error"] = str(exc)[:1000]
        if successes == 0:
            raise RuntimeError("no telephony information could be retrieved")
        return _tool_result(payload)

    async def _sms(self, arguments: dict[str, Any]) -> types.CallToolResult:
        if not self._available(self.sms_command):
            raise RuntimeError("termux-sms-send is not installed")
        number = _normalize_number(arguments.get("number"))
        message = _message(arguments.get("message"))
        await self._run_text(self.sms_command, "-n", number, message)
        return _tool_result({
            "status": "sent",
            "number": number,
            "message_chars": len(message),
            "dispatched_at": _iso(),
        })

    async def _run_json(self, command: str, *args: str) -> Any:
        raw = await self._run_text(command, *args)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{command} returned invalid JSON") from exc

    async def _run_text(self, command: str, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.execution_timeout_sec,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"{command} timed out after {self.execution_timeout_sec:.0f}s"
            ) from exc
        if len(stdout) > _MAX_OUTPUT_BYTES or len(stderr) > _MAX_OUTPUT_BYTES:
            raise RuntimeError(f"{command} exceeded the {_MAX_OUTPUT_BYTES} byte output limit")
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"{command} failed: {detail or f'exit {proc.returncode}'}")
        return stdout.decode("utf-8", errors="replace").strip()


class SSHAndroidPhoneProvider(AndroidPhoneProvider):
    """Server-side MCP provider that reaches one locked-down Termux bridge over SSH."""

    def __init__(
        self,
        *,
        host: str,
        user: str,
        port: int,
        identity_file: str,
        known_hosts_file: str,
        remote_bridge: str,
        ssh_command: str = "/usr/bin/ssh",
        execution_timeout_sec: float = 45.0,
    ) -> None:
        super().__init__(execution_timeout_sec=execution_timeout_sec)
        self.host = str(host).strip()
        self.user = str(user).strip()
        self.port = int(port)
        self.identity_file = str(Path(identity_file).expanduser())
        self.known_hosts_file = str(Path(known_hosts_file).expanduser())
        self.remote_bridge = str(remote_bridge).strip()
        self.ssh_command = str(ssh_command).strip()
        if not self.host or not self.user:
            raise ValueError("SSH phone host and user are required")
        if not 1 <= self.port <= 65535:
            raise ValueError("SSH phone port is invalid")
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", self.remote_bridge):
            raise ValueError("remote phone bridge must be an absolute safe path")

    def _available(self, _command: str) -> bool:
        return True

    async def _location(self, arguments: dict[str, Any]) -> types.CallToolResult:
        provider = str(arguments.get("provider") or "gps").strip().casefold()
        request = str(arguments.get("request") or "once").strip().casefold()
        if provider not in _LOCATION_PROVIDERS:
            raise ValueError(f"unsupported location provider: {provider}")
        if request not in _LOCATION_REQUESTS:
            raise ValueError(f"unsupported location request: {request}")
        return _tool_result(await self._bridge("location", {"provider": provider, "request": request}))

    async def _telephony(self) -> types.CallToolResult:
        return _tool_result(await self._bridge("telephony", {}))

    async def _sms(self, arguments: dict[str, Any]) -> types.CallToolResult:
        number = _normalize_number(arguments.get("number"))
        message = _message(arguments.get("message"))
        return _tool_result(await self._bridge("sms", {"number": number, "message": message}))

    async def _bridge(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = json.dumps({"operation": operation, "payload": payload}, ensure_ascii=False).encode("utf-8")
        args = [
            "-T", "-i", self.identity_file, "-p", str(self.port),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self.known_hosts_file}",
            "-o", "ConnectTimeout=10", f"{self.user}@{self.host}", self.remote_bridge,
        ]
        proc = await asyncio.create_subprocess_exec(
            self.ssh_command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(request), timeout=self.execution_timeout_sec,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError("Android phone bridge timed out") from exc
        if len(stdout) > _MAX_OUTPUT_BYTES or len(stderr) > _MAX_OUTPUT_BYTES:
            raise RuntimeError("Android phone bridge exceeded output limit")
        try:
            response = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Android phone bridge returned invalid JSON: {detail[:1000]}") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            detail = response.get("error") if isinstance(response, dict) else None
            raise RuntimeError(str(detail or "Android phone bridge failed")[:4000])
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Android phone bridge returned an invalid result")
        return result


async def _serve(provider: AndroidPhoneProvider) -> None:
    async def on_list_tools(_ctx, _params):
        return types.ListToolsResult(tools=await provider.list_tools())

    async def on_call_tool(_ctx, params: types.CallToolRequestParams):
        return await provider.call_tool(params.name, dict(params.arguments or {}))

    server = Server(
        "atlas-android-phone",
        version="1.0.0",
        description="Termux:API bridge exposing narrow Android GPS and SIM capabilities to Atlas.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    options = InitializationOptions(
        server_name="atlas-android-phone",
        server_version="1.0.0",
        description="Termux:API bridge exposing narrow Android GPS and SIM capabilities to Atlas.",
        capabilities=server.get_capabilities(NotificationOptions(), {}),
    )
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expose Android GPS and SIM services over stdio MCP")
    parser.add_argument("--location-command", default="termux-location")
    parser.add_argument("--sms-command", default="termux-sms-send")
    parser.add_argument("--deviceinfo-command", default="termux-telephony-deviceinfo")
    parser.add_argument("--cellinfo-command", default="termux-telephony-cellinfo")
    parser.add_argument("--execution-timeout", type=float, default=45.0)
    parser.add_argument("--ssh-host")
    parser.add_argument("--ssh-user")
    parser.add_argument("--ssh-port", type=int, default=8022)
    parser.add_argument("--ssh-key")
    parser.add_argument("--ssh-known-hosts")
    parser.add_argument("--remote-bridge")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    remote_values = [args.ssh_host, args.ssh_user, args.ssh_key, args.ssh_known_hosts, args.remote_bridge]
    if any(remote_values):
        if not all(remote_values):
            raise SystemExit("remote Android phone mode requires host, user, key, known-hosts and bridge")
        provider: AndroidPhoneProvider = SSHAndroidPhoneProvider(
            host=str(args.ssh_host),
            user=str(args.ssh_user),
            port=int(args.ssh_port),
            identity_file=str(args.ssh_key),
            known_hosts_file=str(args.ssh_known_hosts),
            remote_bridge=str(args.remote_bridge),
            execution_timeout_sec=float(args.execution_timeout),
        )
    else:
        provider = AndroidPhoneProvider(
            location_command=str(args.location_command),
            sms_command=str(args.sms_command),
            deviceinfo_command=str(args.deviceinfo_command),
            cellinfo_command=str(args.cellinfo_command),
            execution_timeout_sec=float(args.execution_timeout),
        )
    asyncio.run(_serve(provider))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
