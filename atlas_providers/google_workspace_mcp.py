from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
import mcp.server.stdio


DISCOVERY_DIRECTORY = "https://www.googleapis.com/discovery/v1/apis"
DEFAULT_SERVICES = ("gmail", "drive", "calendar")
DESTRUCTIVE_METHODS = {
    "delete", "batchDelete", "trash", "emptyTrash", "deletePermanent",
}
GWS_INHERITED_ENV = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
)


@dataclass(frozen=True)
class MethodBinding:
    service: str
    resource_path: tuple[str, ...]
    method_name: str
    method: dict[str, Any]

    @property
    def tool_name(self) -> str:
        return "_".join((self.service, *self.resource_path, self.method_name))

    @property
    def schema_path(self) -> str:
        return ".".join((self.service, *self.resource_path, self.method_name))


class GoogleWorkspaceProvider:
    """Discovery-driven MCP facade over the current Google Workspace CLI."""

    def __init__(
        self,
        *,
        gws_command: str,
        services: tuple[str, ...],
        workspace: Path,
        config_dir: Path | None = None,
        keyring_backend: str = "file",
        discovery_timeout_sec: float = 20.0,
        execution_timeout_sec: float = 120.0,
    ) -> None:
        self.gws_command = gws_command
        self.services = services
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config_dir = config_dir.resolve() if config_dir else None
        if self.config_dir is not None:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        self.keyring_backend = str(keyring_backend or "file").strip() or "file"
        self.discovery_timeout_sec = discovery_timeout_sec
        self.execution_timeout_sec = execution_timeout_sec
        self._directory: dict[str, dict[str, Any]] | None = None
        self._documents: dict[str, dict[str, Any]] = {}
        self._bindings: dict[str, MethodBinding] = {}
        self._tools: list[types.Tool] | None = None

    async def list_tools(self) -> list[types.Tool]:
        if self._tools is None:
            self._tools = await asyncio.to_thread(self._build_tools)
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        if name == "google_workspace_discover":
            return self._discover(arguments)
        if not self._bindings:
            await self.list_tools()
        binding = self._bindings.get(name)
        if binding is None:
            return _tool_error(f"Unknown Google Workspace tool: {name}")
        return await asyncio.to_thread(self._execute, binding, arguments)

    def _load_directory(self) -> dict[str, dict[str, Any]]:
        if self._directory is not None:
            return self._directory
        payload = _fetch_json(DISCOVERY_DIRECTORY, self.discovery_timeout_sec)
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise RuntimeError("Google Discovery directory returned no API list")
        directory: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            current = directory.get(name)
            if current is None or item.get("preferred") is True:
                directory[name] = item
        self._directory = directory
        return directory

    def _document(self, service: str) -> dict[str, Any]:
        cached = self._documents.get(service)
        if cached is not None:
            return cached
        entry = self._load_directory().get(service)
        if entry is None:
            raise RuntimeError(f"Google Discovery service not found: {service}")
        url = str(entry.get("discoveryRestUrl") or "").strip()
        if not url:
            raise RuntimeError(f"Google Discovery service has no REST document: {service}")
        document = _fetch_json(url, self.discovery_timeout_sec)
        self._documents[service] = document
        return document

    def _build_tools(self) -> list[types.Tool]:
        tools: list[types.Tool] = [_discover_tool(self.services)]
        self._bindings.clear()
        for service in self.services:
            document = self._document(service)
            resources = document.get("resources") if isinstance(document, dict) else None
            if isinstance(resources, dict):
                self._walk_resources(service, (), resources, tools)
        return tools

    def _walk_resources(
        self,
        service: str,
        prefix: tuple[str, ...],
        resources: dict[str, Any],
        tools: list[types.Tool],
    ) -> None:
        for resource_name in sorted(resources):
            resource = resources[resource_name]
            if not isinstance(resource, dict):
                continue
            path = (*prefix, resource_name)
            methods = resource.get("methods")
            if isinstance(methods, dict):
                for method_name in sorted(methods):
                    method = methods[method_name]
                    if not isinstance(method, dict):
                        continue
                    binding = MethodBinding(service, path, method_name, method)
                    if binding.tool_name in self._bindings:
                        raise RuntimeError(f"duplicate Google Workspace tool name: {binding.tool_name}")
                    self._bindings[binding.tool_name] = binding
                    tools.append(_tool_for(binding))
            children = resource.get("resources")
            if isinstance(children, dict):
                self._walk_resources(service, path, children, tools)

    def _discover(self, arguments: dict[str, Any]) -> types.CallToolResult:
        service = str(arguments.get("service") or "").strip()
        resource = str(arguments.get("resource") or "").strip()
        method = str(arguments.get("method") or "").strip()
        if service not in self.services:
            return _tool_error(f"Service is not enabled: {service}")
        document = self._document(service)
        payload = _discovery_view(document, service=service, resource=resource, method=method)
        return _tool_success(payload)

    def _execute(self, binding: MethodBinding, arguments: dict[str, Any]) -> types.CallToolResult:
        command = [
            self.gws_command,
            binding.service,
            *binding.resource_path,
            binding.method_name,
        ]
        params = arguments.get("params")
        body = arguments.get("body")
        if params is not None:
            if not isinstance(params, dict):
                return _tool_error("params must be an object")
            command.extend(("--params", _compact_json(params)))
        if body is not None:
            if not isinstance(body, dict):
                return _tool_error("body must be an object")
            if body:
                command.extend(("--json", _compact_json(body)))
        upload = arguments.get("upload")
        if upload:
            try:
                command.extend(("--upload", _provider_upload_path(self.workspace, str(upload))))
            except ValueError as exc:
                return _tool_error(str(exc))
        upload_type = str(arguments.get("upload_content_type") or "").strip()
        if upload_type:
            command.extend(("--upload-content-type", upload_type))
        output = arguments.get("output")
        if binding.method.get("supportsMediaDownload") is True and not output:
            return _tool_error("output is required for media-download methods")
        if output:
            try:
                command.extend(("--output", _provider_output_path(self.workspace, str(output))))
            except ValueError as exc:
                return _tool_error(str(exc))
        if arguments.get("page_all") is True:
            command.append("--page-all")
        if arguments.get("page_limit") is not None:
            command.extend(("--page-limit", str(int(arguments["page_limit"]))))
        if arguments.get("page_delay_ms") is not None:
            command.extend(("--page-delay", str(int(arguments["page_delay_ms"]))))

        try:
            env = {key: os.environ[key] for key in GWS_INHERITED_ENV if key in os.environ}
            if self.config_dir is not None:
                env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = str(self.config_dir)
            env["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] = self.keyring_backend
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.execution_timeout_sec,
                check=False,
            )
        except FileNotFoundError:
            return _tool_error(f"gws executable not found: {self.gws_command}")
        except subprocess.TimeoutExpired:
            return _tool_error(f"gws execution exceeded {self.execution_timeout_sec:.0f}s")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "gws command failed").strip()
            return _tool_error(detail)
        return _tool_success(_parse_gws_output(completed.stdout), text=completed.stdout.strip() or None)


def _fetch_json(url: str, timeout_sec: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "Atlas-Google-Workspace-MCP/1"})
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("Google Discovery returned a non-object payload")
    return payload


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _relative_parts(value: str) -> tuple[str, ...]:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("provider file paths must be relative and remain inside its workspace")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts:
        raise ValueError("provider file path must name a file")
    return parts


def _reject_symlink_components(base: Path, parts: tuple[str, ...], *, include_leaf: bool) -> None:
    current = base
    stop = len(parts) if include_leaf else max(0, len(parts) - 1)
    for part in parts[:stop]:
        current = current / part
        if current.is_symlink():
            raise ValueError("provider file paths must not traverse symlinks")


def _provider_upload_path(workspace: Path, value: str) -> str:
    base = workspace.resolve(strict=True)
    parts = _relative_parts(value)
    _reject_symlink_components(base, parts, include_leaf=True)
    target = (base.joinpath(*parts)).resolve(strict=True)
    if not target.is_relative_to(base) or not target.is_file():
        raise ValueError("upload must be a regular file inside the provider workspace")
    return target.relative_to(base).as_posix()


def _provider_output_path(workspace: Path, value: str) -> str:
    base = workspace.resolve(strict=True)
    parts = _relative_parts(value)
    _reject_symlink_components(base, parts, include_leaf=True)
    target = base.joinpath(*parts)
    parent = target.parent.resolve(strict=True)
    if not parent.is_relative_to(base) or not parent.is_dir():
        raise ValueError("output parent must be a directory inside the provider workspace")
    if target.exists() or target.is_symlink():
        raise ValueError("output path already exists; provider downloads do not overwrite")
    return target.relative_to(base).as_posix()


def _tool_for(binding: MethodBinding) -> types.Tool:
    method = binding.method
    parameters = method.get("parameters") if isinstance(method.get("parameters"), dict) else {}
    param_properties: dict[str, Any] = {}
    required_params: list[str] = []
    for name, raw in sorted(parameters.items()):
        if not isinstance(raw, dict):
            continue
        param_properties[name] = _parameter_schema(raw)
        if raw.get("required") is True:
            required_params.append(name)
    params_schema: dict[str, Any] = {
        "type": "object",
        "properties": param_properties,
        "additionalProperties": True,
    }
    if required_params:
        params_schema["required"] = required_params
    properties: dict[str, Any] = {
        "params": {
            **params_schema,
            "description": "Google API path/query parameters.",
        },
        "page_all": {"type": "boolean", "description": "Fetch every available page."},
        "page_limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        "page_delay_ms": {"type": "integer", "minimum": 0, "maximum": 60000},
    }
    if method.get("supportsMediaDownload") is True:
        properties["output"] = {
            "type": "string",
            "description": "Required relative provider-workspace path for binary output; must not already exist.",
        }
    if method.get("request") is not None:
        request_name = _schema_ref_name(method.get("request"))
        properties["body"] = {
            "type": "object",
            "additionalProperties": True,
            "description": f"Google API request body{f' ({request_name})' if request_name else ''}.",
        }
    if method.get("supportsMediaUpload") is True:
        properties["upload"] = {
            "type": "string",
            "description": "Relative provider-workspace file to upload.",
        }
        properties["upload_content_type"] = {
            "type": "string",
            "description": "Optional MIME type for uploaded media.",
        }
    description = str(method.get("description") or "").strip()
    title = f"{binding.schema_path}: {description}" if description else binding.schema_path
    annotations = types.ToolAnnotations(
        readOnlyHint=str(method.get("httpMethod") or "").upper() == "GET",
        destructiveHint=_is_destructive(binding),
        openWorldHint=True,
    )
    return types.Tool(
        name=binding.tool_name,
        description=title,
        inputSchema={"type": "object", "properties": properties, "additionalProperties": False},
        annotations=annotations,
    )


def _parameter_schema(raw: dict[str, Any]) -> dict[str, Any]:
    kind = str(raw.get("type") or "string")
    schema: dict[str, Any]
    if raw.get("repeated") is True:
        schema = {"type": "array", "items": {"type": _json_type(kind)}}
    else:
        schema = {"type": _json_type(kind)}
    description = str(raw.get("description") or "").strip()
    if description:
        schema["description"] = description
    enum = raw.get("enum")
    if isinstance(enum, list) and enum:
        schema["enum"] = enum
    return schema


def _json_type(kind: str) -> str:
    return {"integer": "integer", "number": "number", "boolean": "boolean", "object": "object"}.get(kind, "string")


def _schema_ref_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    ref = value.get("$ref")
    return str(ref) if ref else None


def _is_destructive(binding: MethodBinding) -> bool:
    http_method = str(binding.method.get("httpMethod") or "").upper()
    if http_method == "DELETE":
        return True
    name = binding.method_name
    lowered = name.casefold()
    return name in DESTRUCTIVE_METHODS or "delete" in lowered or "trash" in lowered


def _discover_tool(services: tuple[str, ...]) -> types.Tool:
    return types.Tool(
        name="google_workspace_discover",
        description="Inspect the live Google Discovery schema for an enabled Workspace service, resource, or method.",
        inputSchema={
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": list(services)},
                "resource": {"type": "string", "description": "Dot-separated resource path, for example users.messages."},
                "method": {"type": "string", "description": "Optional method name within the resource."},
            },
            "required": ["service"],
            "additionalProperties": False,
        },
        annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )


def _discovery_view(document: dict[str, Any], *, service: str, resource: str, method: str) -> dict[str, Any]:
    resources = document.get("resources") if isinstance(document.get("resources"), dict) else {}
    if not resource:
        entries: list[dict[str, Any]] = []
        _collect_resources(resources, (), entries)
        return {"service": service, "resources": entries}
    target = _find_resource(resources, tuple(part for part in resource.split(".") if part))
    if target is None:
        raise ValueError(f"Google Workspace resource not found: {service}.{resource}")
    methods = target.get("methods") if isinstance(target.get("methods"), dict) else {}
    if not method:
        return {
            "service": service,
            "resource": resource,
            "methods": [
                {"name": name, "httpMethod": item.get("httpMethod"), "description": item.get("description")}
                for name, item in sorted(methods.items()) if isinstance(item, dict)
            ],
        }
    item = methods.get(method)
    if not isinstance(item, dict):
        raise ValueError(f"Google Workspace method not found: {service}.{resource}.{method}")
    return {
        "service": service,
        "resource": resource,
        "method": method,
        "httpMethod": item.get("httpMethod"),
        "path": item.get("path"),
        "description": item.get("description"),
        "parameters": item.get("parameters") or {},
        "request": item.get("request"),
        "response": item.get("response"),
        "supportsMediaUpload": bool(item.get("supportsMediaUpload")),
        "supportsMediaDownload": bool(item.get("supportsMediaDownload")),
        "scopes": item.get("scopes") or [],
    }


def _collect_resources(resources: dict[str, Any], prefix: tuple[str, ...], out: list[dict[str, Any]]) -> None:
    for name, resource in sorted(resources.items()):
        if not isinstance(resource, dict):
            continue
        path = (*prefix, name)
        methods = resource.get("methods") if isinstance(resource.get("methods"), dict) else {}
        if methods:
            out.append({"name": ".".join(path), "methods": sorted(methods)})
        children = resource.get("resources")
        if isinstance(children, dict):
            _collect_resources(children, path, out)


def _find_resource(resources: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any] | None:
    current = resources
    target: dict[str, Any] | None = None
    for part in path:
        value = current.get(part)
        if not isinstance(value, dict):
            return None
        target = value
        current = value.get("resources") if isinstance(value.get("resources"), dict) else {}
    return target


def _parse_gws_output(text: str) -> Any:
    raw = text.strip()
    if not raw:
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    pages: list[Any] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pages.append(json.loads(line))
        except json.JSONDecodeError:
            return {"text": raw}
    if pages:
        return {"pages": pages}
    return {"text": raw}


def _tool_success(payload: Any, *, text: str | None = None) -> types.CallToolResult:
    structured = payload if isinstance(payload, dict) else {"result": payload}
    rendered = text or json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return types.CallToolResult(
        content=[types.TextContent(text=rendered)],
        structuredContent=structured,
        isError=False,
    )


def _tool_error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text=str(message))],
        structuredContent={"error": str(message)},
        isError=True,
    )


async def _serve(provider: GoogleWorkspaceProvider) -> None:
    async def on_list_tools(_ctx, _params):
        return types.ListToolsResult(tools=await provider.list_tools())

    async def on_call_tool(_ctx, params: types.CallToolRequestParams):
        try:
            return await provider.call_tool(params.name, dict(params.arguments or {}))
        except Exception as exc:
            return _tool_error(str(exc))

    server = Server(
        "atlas-google-workspace",
        version="1.0.0",
        description="Discovery-driven Google Workspace MCP provider for Atlas.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    options = InitializationOptions(
        server_name="atlas-google-workspace",
        server_version="1.0.0",
        description="Discovery-driven Google Workspace MCP provider for Atlas.",
        capabilities=server.get_capabilities(NotificationOptions(), {}),
    )
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expose current gws Google Workspace APIs over stdio MCP")
    parser.add_argument("--gws", required=True, help="Path or executable name for the current gws CLI")
    parser.add_argument("--services", default=",".join(DEFAULT_SERVICES), help="Comma-separated Google Discovery service names")
    parser.add_argument("--workspace", required=True, help="Provider-local workspace for upload/download paths")
    parser.add_argument("--config-dir", help="Provider-local gws configuration/credential directory")
    parser.add_argument("--keyring-backend", default="file", help="gws credential keyring backend for headless execution")
    parser.add_argument("--discovery-timeout", type=float, default=20.0)
    parser.add_argument("--execution-timeout", type=float, default=120.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    services = tuple(dict.fromkeys(part.strip() for part in args.services.split(",") if part.strip()))
    if not services:
        raise SystemExit("at least one Google Workspace service is required")
    provider = GoogleWorkspaceProvider(
        gws_command=str(args.gws),
        services=services,
        workspace=Path(args.workspace),
        config_dir=Path(args.config_dir) if args.config_dir else None,
        keyring_backend=str(args.keyring_backend),
        discovery_timeout_sec=float(args.discovery_timeout),
        execution_timeout_sec=float(args.execution_timeout),
    )
    asyncio.run(_serve(provider))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
