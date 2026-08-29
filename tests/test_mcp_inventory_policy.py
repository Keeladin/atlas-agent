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


def test_mcp_discovers_every_tool_but_policy_still_controls_execution(tmp_path, monkeypatch):
    rt = build_runtime(tmp_path / "instance")
    fake = FakeMCP()
    monkeypatch.setattr(rt.mcp, "_client", lambda server: fake)
    rt.mcp_store.put(
        server_id="demo",
        display_name="Demo MCP",
        kind="n8n",
        url="http://127.0.0.1:5678/mcp",
    )
    tools = rt.mcp.refresh("demo")
    assert {tool.name for tool in tools} == {"read_data", "delete_data"}
    assert rt.capabilities_registry.get("mcp.demo.read_data").definition.source == "n8n"
    assert rt.capabilities_registry.get("mcp.demo.delete_data").definition.effect_class == "destructive"

    owner = rt.identities.current_owner().principal_id
    rt.seed_policy()
    rt.policy_store.set(
        principal_id=owner,
        scope="mcp/demo/tool/read_data",
        operation="invoke",
        decision="YES",
    )
    provenance = InvocationProvenance(owner, "human", "chat")

    read = rt.capabilities.invoke("mcp.demo.read_data", {}, provenance=provenance)
    assert read.status == "succeeded"
    assert fake.calls == [("read_data", {})]

    destructive = rt.capabilities.invoke("mcp.demo.delete_data", {}, provenance=provenance)
    assert destructive.status == "pending_confirmation"
    assert fake.calls == [("read_data", {})]
    confirmed = rt.actions.confirm(destructive.occurrence_id, principal_id=owner)
    assert confirmed.status == "succeeded"
    assert fake.calls[-1] == ("delete_data", {})


def test_mcp_server_id_must_be_policy_safe(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    with pytest.raises(ValueError):
        rt.mcp_store.put(
            server_id="../other",
            display_name="Bad",
            kind="mcp",
            url="http://127.0.0.1:9999/mcp",
        )
