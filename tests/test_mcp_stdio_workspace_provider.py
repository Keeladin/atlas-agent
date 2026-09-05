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


def test_mcp_store_refuses_pre_cutover_transport_schema(tmp_path):
    db_path = tmp_path / "identity.db"
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE mcp_servers(
            server_id TEXT PRIMARY KEY,display_name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('mcp','n8n')),
            transport TEXT NOT NULL CHECK(transport IN ('streamable-http')),
            url TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
            credential_ref TEXT,timeout_sec REAL NOT NULL DEFAULT 30,
            read_timeout_sec REAL NOT NULL DEFAULT 300,last_error TEXT,
            last_discovered_at TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
    store = MCPServerStore(db_path)
    with pytest.raises(RuntimeError, match="development schema reset"):
        store.initialize()

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
    credentials_file = tmp_path / "authorized-user.json"
    credentials_file.write_text('{"type":"authorized_user"}')
    provider = workspace.GoogleWorkspaceProvider(
        gws_command="/opt/gws",
        services=("gmail",),
        workspace=tmp_path / "workspace",
        config_dir=tmp_path / "config",
        credentials_file=credentials_file,
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
    assert captured["env"]["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] == str(provider.credentials_file)
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
    assert workspace._provider_output_path(root, "existing.bin") == "existing-2.bin"
    assert existing.read_bytes() == b"old"
    (root / "existing-2.bin").write_bytes(b"newer")
    assert workspace._provider_output_path(root, "existing.bin") == "existing-3.bin"

    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (root / "escape").symlink_to(outside_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        workspace._provider_output_path(root, "escape/new.bin")


def test_gmail_message_get_is_normalized_for_agent_context():
    import base64
    body = base64.urlsafe_b64encode(b"Hello from the body.").decode().rstrip("=")
    message = {
        "id": "msg-1", "threadId": "thr-1", "historyId": "7",
        "internalDate": "1788069607000", "labelIds": ["INBOX"], "snippet": "Hello",
        "sizeEstimate": 1234,
        "payload": {
            "headers": [
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "owner@example.com"},
                {"name": "Subject", "value": "Useful subject"},
                {"name": "Date", "value": "Sun, 30 Aug 2026 06:00:07 +0000"},
                {"name": "Received", "value": "transport noise" * 500},
            ],
            "parts": [{"mimeType": "text/plain", "body": {"data": body}, "headers": []}],
        },
    }
    result = workspace._normalize_gmail_message(message)
    assert result["subject"] == "Useful subject"
    assert result["from"] == "Sender <sender@example.com>"
    assert result["body_text"] == "Hello from the body."
    assert result["received_at"] == "2026-08-30T06:00:07Z"
    assert "transport noise" not in str(result)


def test_gmail_thread_get_reuses_message_normalizer():
    import base64
    body = base64.urlsafe_b64encode(b"Thread body").decode().rstrip("=")
    raw = {"id":"thr-1","historyId":"9","messages":[{
        "id":"msg-1","threadId":"thr-1","internalDate":"1788069607000","snippet":"stub",
        "payload":{"headers":[{"name":"Subject","value":"Real subject"}],
                   "parts":[{"mimeType":"text/plain","body":{"data":body},"headers":[]}]},
    }]}
    result = workspace._normalize_gmail_thread(raw)
    assert result["thread_id"] == "thr-1"
    assert result["messages"][0]["subject"] == "Real subject"
    assert result["messages"][0]["body_text"] == "Thread body"


def test_drive_metadata_get_does_not_require_output(tmp_path, monkeypatch):
    provider = workspace.GoogleWorkspaceProvider(gws_command="gws", services=("drive",), workspace=tmp_path / "workspace")
    binding = workspace.MethodBinding("drive", ("files",), "get", {"httpMethod":"GET","supportsMediaDownload":True})
    captured = {}
    def fake_run(command, **kwargs):
        captured["command"] = command
        return workspace.subprocess.CompletedProcess(command, 0, '{"id":"file-1","name":"thing.zip"}\n', "")
    monkeypatch.setattr(workspace.subprocess, "run", fake_run)
    result = provider._execute(binding, {"params":{"fileId":"file-1","fields":"*"}})
    assert result.is_error is False
    assert "--output" not in captured["command"]


def test_drive_media_get_requires_output(tmp_path):
    provider = workspace.GoogleWorkspaceProvider(gws_command="gws", services=("drive",), workspace=tmp_path / "workspace")
    binding = workspace.MethodBinding("drive", ("files",), "get", {"httpMethod":"GET","supportsMediaDownload":True})
    result = provider._execute(binding, {"params":{"fileId":"file-1","alt":"media"}})
    assert result.is_error is True
    assert "output is required" in result.content[0].text


def test_discovery_cache_serves_fresh_disk_copy_without_network(tmp_path, monkeypatch):
    provider = workspace.GoogleWorkspaceProvider(gws_command="gws", services=("drive",), workspace=tmp_path / "workspace")
    cache = provider._discovery_cache_dir / "directory.json"
    cache.write_text('{"items":[]}')
    monkeypatch.setattr(workspace, "_fetch_json", lambda *_: (_ for _ in ()).throw(AssertionError("network should not be used")))
    assert provider._cached_discovery("directory", "https://example.invalid") == {"items": []}


def _write_pid_server(path: Path) -> None:
    path.write_text("""import asyncio, os
import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
async def listing(_ctx,_params):
    return types.ListToolsResult(tools=[types.Tool(name='pid',description='pid',inputSchema={'type':'object'}),types.Tool(name='slow',description='slow',inputSchema={'type':'object'})])
async def calling(_ctx,params):
    if params.name == 'slow': await asyncio.sleep(5)
    return types.CallToolResult(content=[types.TextContent(text=str(os.getpid()))],structuredContent={'pid':os.getpid()})
async def main():
    server=Server('pid',version='1',on_list_tools=listing,on_call_tool=calling)
    opts=InitializationOptions(server_name='pid',server_version='1',capabilities=server.get_capabilities(NotificationOptions(),{}))
    async with mcp.server.stdio.stdio_server() as (r,w): await server.run(r,w,opts)
asyncio.run(main())
""")


def test_stdio_client_reuses_session_and_closes_child_on_timeout(tmp_path):
    import os, time
    from atlas_core.mcp_stdio import StdioMCPClient
    script=tmp_path/'pid_server.py';_write_pid_server(script)
    client=StdioMCPClient(sys.executable,args=[str(script)],timeout_sec=1.0,read_timeout_sec=0.2)
    first=client.call_tool('pid',{})['structuredContent']['pid']
    second=client.call_tool('pid',{})['structuredContent']['pid']
    assert first == second
    with pytest.raises(Exception): client.call_tool('slow',{})
    for _ in range(20):
        try: os.kill(first,0)
        except ProcessLookupError: break
        time.sleep(0.05)
    else: pytest.fail('timed-out stdio MCP child remained alive')
