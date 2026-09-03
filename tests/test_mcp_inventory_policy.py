from __future__ import annotations

import pytest

from atlas_api.compose import build_runtime
from atlas_core.provenance import InvocationProvenance


class FakeMCP:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return [
            {
                "name": "read_data",
                "description": "Read provider data",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "delete_data",
                "description": "Delete provider data",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": {"destructiveHint": True},
            },
        ]

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return {"isError": False, "structuredContent": {"tool": name, "ok": True}}


def test_mcp_discovery_never_grants_authority_and_parent_domain_can_be_granted_once(tmp_path, monkeypatch):
    rt = build_runtime(tmp_path / "instance")
    fake = FakeMCP()
    monkeypatch.setattr(rt.mcp, "_client", lambda server: fake)
    rt.mcp_store.put(server_id="demo", display_name="Demo MCP", kind="n8n", url="http://127.0.0.1:5678/mcp")
    tools = rt.mcp.refresh("demo")
    assert {tool.name for tool in tools} == {"read_data", "delete_data"}
    assert rt.capabilities_registry.get("mcp.demo.read_data").definition.source == "n8n"
    assert rt.capabilities_registry.get("mcp.demo.delete_data").definition.effect_class == "destructive"

    owner = rt.identities.current_owner().principal_id
    rt.seed_policy()
    provenance = InvocationProvenance(owner, "human", "chat")

    # Discovery populated the registry, but no MCP policy row was created.
    assert rt.policy.resolve(principal_id=owner, scope="mcp/demo/tool/read_data", operation="invoke").decision == "NO"
    assert rt.capabilities.invoke("mcp.demo.read_data", {}, provenance=provenance).status == "blocked"
    assert rt.capabilities.invoke("mcp.demo.delete_data", {}, provenance=provenance).status == "blocked"
    assert fake.calls == []

    # One human-readable service grant covers the registered tool set.
    rt.policy_store.set(principal_id=owner, scope="mcp/demo", operation="*", decision="YES")
    assert rt.capabilities.invoke("mcp.demo.read_data", {}, provenance=provenance).status == "succeeded"
    assert rt.capabilities.invoke("mcp.demo.delete_data", {}, provenance=provenance).status == "succeeded"
    assert fake.calls == [("read_data", {}), ("delete_data", {})]

    # Rare narrow exceptions remain possible without turning Policy into a tool registry.
    rt.policy_store.set(principal_id=owner, scope="mcp/demo/tool/delete_data", operation="invoke", decision="NO")
    assert rt.capabilities.invoke("mcp.demo.delete_data", {}, provenance=provenance).status == "blocked"
    assert fake.calls == [("read_data", {}), ("delete_data", {})]


def test_mcp_server_id_must_be_policy_safe(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    with pytest.raises(ValueError):
        rt.mcp_store.put(
            server_id="../other",
            display_name="Bad",
            kind="mcp",
            url="http://127.0.0.1:9999/mcp",
        )


def test_failed_refresh_keeps_previous_inventory(tmp_path, monkeypatch):
    rt = build_runtime(tmp_path / "instance")
    fake = FakeMCP()
    monkeypatch.setattr(rt.mcp, "_client", lambda server: fake)
    rt.mcp_store.put(server_id="demo", display_name="Demo", kind="mcp", url="http://127.0.0.1:9999/mcp")
    rt.mcp.refresh("demo")
    assert rt.capabilities_registry.get("mcp.demo.read_data")
    def broken(): raise RuntimeError("temporary discovery failure")
    fake.list_tools = broken
    with pytest.raises(RuntimeError, match="temporary discovery failure"):
        rt.mcp.refresh("demo")
    assert rt.capabilities_registry.get("mcp.demo.read_data")
    available, reason = rt.capabilities_registry.get("mcp.demo.read_data").availability()
    assert available is True
    assert reason.startswith("stale_inventory:")


def test_mcp_safe_id_collision_is_rejected_without_replacing_inventory(tmp_path, monkeypatch):
    rt = build_runtime(tmp_path / "instance")
    fake = FakeMCP()
    monkeypatch.setattr(rt.mcp, "_client", lambda server: fake)
    rt.mcp_store.put(server_id="demo", display_name="Demo", kind="mcp", url="http://127.0.0.1:9999/mcp")
    rt.mcp.refresh("demo")
    fake.list_tools = lambda: [
        {"name":"Send Digest","inputSchema":{},"annotations":{}},
        {"name":"Send/Digest","inputSchema":{},"annotations":{}},
    ]
    with pytest.raises(RuntimeError, match="collision"):
        rt.mcp.refresh("demo")
    assert rt.capabilities_registry.get("mcp.demo.read_data")


def test_mcp_provider_error_detail_is_preserved(tmp_path, monkeypatch):
    rt = build_runtime(tmp_path / "instance")
    fake = FakeMCP()
    fake.call_tool = lambda name, arguments: {
        "isError": True,
        "content": [{"type":"text","text":"userId is required"}],
        "structuredContent": {"error":"userId is required"},
    }
    monkeypatch.setattr(rt.mcp, "_client", lambda server: fake)
    rt.mcp_store.put(server_id="demo", display_name="Demo", kind="mcp", url="http://127.0.0.1:9999/mcp")
    result = rt.mcp.call_tool("demo", "read_data", {})
    assert result.ok is False
    assert result.error == "userId is required"
