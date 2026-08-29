from __future__ import annotations

import asyncio
import sqlite3
import sys

import pytest
from pathlib import Path

from atlas_api.compose import build_runtime
from atlas_core.mcp import MCPServerStore
from atlas_core.provenance import InvocationProvenance
from atlas_providers import google_workspace_mcp as workspace


def _write_echo_server(path: Path) -> None:
    path.write_text(
        """import asyncio
import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio

async def listing(_ctx, _params):
    return types.ListToolsResult(tools=[types.Tool(name='echo', description='Echo input', inputSchema={'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}, annotations=types.ToolAnnotations(readOnlyHint=True))])

async def calling(_ctx, params):
    value = (params.arguments or {}).get('value', '')
    return types.CallToolResult(content=[types.TextContent(text=value)], structuredContent={'value': value})

async def main():
    server = Server('echo', version='1', on_list_tools=listing, on_call_tool=calling)
    opts = InitializationOptions(server_name='echo', server_version='1', capabilities=server.get_capabilities(NotificationOptions(), {}))
    async with mcp.server.stdio.stdio_server() as (r, w):
        await server.run(r, w, opts)

asyncio.run(main())
""",
        encoding="utf-8",
    )


def test_stdio_mcp_is_discovered_and_policy_gates_invocation(tmp_path):
    server_script = tmp_path / "echo_server.py"
    _write_echo_server(server_script)
    rt = build_runtime(tmp_path / "instance")
    rt.mcp_store.put(
        server_id="echo",
        display_name="Echo stdio",
        kind="mcp",
        transport="stdio",
        command=sys.executable,
        args=[str(server_script)],
    )
    tools = rt.mcp.refresh("echo")
    assert [tool.name for tool in tools] == ["echo"]
    owner = rt.identities.current_owner().principal_id
    rt.seed_policy()
    rt.policy_store.set(
        principal_id=owner,
        scope="mcp/echo/tool/echo",
        operation="invoke",
        decision="YES",
    )
    result = rt.capabilities.invoke(
        "mcp.echo.echo",
        {"value": "hello"},
        provenance=InvocationProvenance(owner, "human", "control"),
    )
    assert result.status == "succeeded"
    assert result.result["structuredContent"]["value"] == "hello"


def test_mcp_store_migrates_existing_http_only_schema(tmp_path):
    db_path = tmp_path / "identity.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE mcp_servers(
                server_id TEXT PRIMARY KEY,display_name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('mcp','n8n')),
                transport TEXT NOT NULL CHECK(transport IN ('streamable-http')),
                url TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
                credential_ref TEXT,timeout_sec REAL NOT NULL DEFAULT 30,
                read_timeout_sec REAL NOT NULL DEFAULT 300,last_error TEXT,
                last_discovered_at TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        db.execute(
            "INSERT INTO mcp_servers(server_id,display_name,kind,transport,url) VALUES ('old','Old HTTP','mcp','streamable-http','http://127.0.0.1:9999/mcp')"
        )
    store = MCPServerStore(db_path)
    store.initialize()
    assert store.get("old").url == "http://127.0.0.1:9999/mcp"
    stdio = store.put(
        server_id="local",
        display_name="Local",
        transport="stdio",
        command="/usr/bin/python3",
        args=["server.py"],
    )
    assert stdio.transport == "stdio"
    assert stdio.command == "/usr/bin/python3"
    assert stdio.args == ("server.py",)


def test_workspace_provider_maps_google_discovery_methods_to_tools(tmp_path, monkeypatch):
    directory = {
        "items": [
            {"name": "gmail", "preferred": True, "discoveryRestUrl": "https://example/gmail"},
            {"name": "drive", "preferred": True, "discoveryRestUrl": "https://example/drive"},
            {"name": "calendar", "preferred": True, "discoveryRestUrl": "https://example/calendar"},
        ]
    }
    gmail = {
        "resources": {
            "users": {
                "resources": {
                    "messages": {
                        "methods": {
                            "send": {"httpMethod": "POST", "description": "Sends a message", "request": {"$ref": "Message"}, "parameters": {"userId": {"type": "string", "required": True}}},
                            "delete": {"httpMethod": "DELETE", "description": "Deletes a message", "parameters": {"id": {"type": "string", "required": True}}},
                        }
                    }
                }
            }
        }
    }
    drive = {"resources": {"files": {"methods": {"list": {"httpMethod": "GET", "description": "Lists files"}}}}}
    calendar = {"resources": {"events": {"methods": {"insert": {"httpMethod": "POST", "description": "Creates an event", "request": {"$ref": "Event"}}}}}}
    payloads = {
        workspace.DISCOVERY_DIRECTORY: directory,
        "https://example/gmail": gmail,
        "https://example/drive": drive,
        "https://example/calendar": calendar,
    }
    monkeypatch.setattr(workspace, "_fetch_json", lambda url, _timeout: payloads[url])
    provider = workspace.GoogleWorkspaceProvider(
        gws_command="gws",
        services=("gmail", "drive", "calendar"),
        workspace=tmp_path / "workspace",
    )
    tools = asyncio.run(provider.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert {"google_workspace_discover", "gmail_users_messages_send", "gmail_users_messages_delete", "drive_files_list", "calendar_events_insert"} <= set(by_name)
    assert by_name["drive_files_list"].annotations.read_only_hint is True
    assert by_name["gmail_users_messages_delete"].annotations.destructive_hint is True
    send_params = by_name["gmail_users_messages_send"].input_schema["properties"]["params"]
    assert send_params["required"] == ["userId"]


def test_workspace_provider_executes_gws_with_bounded_environment(tmp_path, monkeypatch):
    provider = workspace.GoogleWorkspaceProvider(
        gws_command="/opt/gws",
        services=("gmail",),
        workspace=tmp_path / "workspace",
        config_dir=tmp_path / "config",
    )
    binding = workspace.MethodBinding(
        "gmail",
        ("users", "messages"),
        "send",
        {"httpMethod": "POST", "request": {"$ref": "Message"}},
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return workspace.subprocess.CompletedProcess(command, 0, '{"id":"message-1"}\n', "")

    monkeypatch.setenv("ATLAS_COMPANION_PASSWORD", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    monkeypatch.setattr(workspace.subprocess, "run", fake_run)
    result = provider._execute(binding, {"params": {"userId": "me"}, "body": {"raw": "abc"}})

    assert result.is_error is False
    assert captured["command"] == [
        "/opt/gws", "gmail", "users", "messages", "send",
        "--params", '{"userId":"me"}', "--json", '{"raw":"abc"}',
    ]
    assert captured["cwd"] == provider.workspace
    assert captured["env"]["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] == str(provider.config_dir)
    assert captured["env"]["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] == "file"
    assert captured["env"]["HTTPS_PROXY"] == "http://proxy.example"
    assert "ATLAS_COMPANION_PASSWORD" not in captured["env"]


def test_workspace_provider_file_paths_cannot_escape_or_overwrite(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    upload = root / "upload.txt"
    upload.write_text("hello")
    assert workspace._provider_upload_path(root, "upload.txt") == "upload.txt"

    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (root / "link.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        workspace._provider_upload_path(root, "link.txt")

    existing = root / "existing.bin"
    existing.write_bytes(b"old")
    with pytest.raises(ValueError, match="already exists"):
        workspace._provider_output_path(root, "existing.bin")

    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (root / "escape").symlink_to(outside_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        workspace._provider_output_path(root, "escape/new.bin")
