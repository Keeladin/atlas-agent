from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any

from atlas_core.work import build_work_runtime
from atlas_core.integrations.n8n_mcp import (
    DEFAULT_SECRET_REF,
    DEFAULT_URL,
    N8NMCPConfig,
    N8NMCPProvider,
)
from atlas_core.tools import ToolGateway


TOKEN = "n8n-mcp-test-token-do-not-leak"
PROVIDER_SOURCE = Path(__file__).resolve().parents[1] / "atlas_core" / "integrations" / "n8n_mcp.py"


class FakeMCP:
    def __init__(self, tools: list[dict[str, Any]] | None = None) -> None:
        self.tools = tools or [
            {
                "name": "search_workflows",
                "description": "Search workflows",
                "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
        ]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self.tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        return {"content": [{"type": "text", "text": name}], "isError": False}


class FailingMCP:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def list_tools(self) -> list[dict[str, Any]]:
        raise self.error

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise self.error


def _factory(client, captured: dict[str, Any] | None = None):
    def factory(url: str, *, headers, timeout_sec, read_timeout_sec):
        if captured is not None:
            captured.update(
                {
                    "url": url,
                    "headers": dict(headers),
                    "timeout_sec": timeout_sec,
                    "read_timeout_sec": read_timeout_sec,
                }
            )
        return client

    return factory


class N8NMCPProviderTests(unittest.TestCase):
    def test_provider_has_no_legacy_runtime_dependencies(self) -> None:
        source = PROVIDER_SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "atlas_core.runtime",
            "atlas_core.bootstrap",
            "atlas_core.tasks",
            "atlas_core.providers",
            "atlas_companion",
            "CredentialStore",
            "TaskRuntime",
            "build_runtime",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_disabled_configuration_does_not_connect(self) -> None:
        captured: dict[str, Any] = {}
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=False),
            environ={DEFAULT_SECRET_REF: TOKEN},
            client_factory=_factory(FakeMCP(), captured),
        )
        gateway = ToolGateway()
        status = provider.connect(gateway)
        self.assertTrue(status.configured)
        self.assertFalse(status.enabled)
        self.assertFalse(status.available)
        self.assertIsNone(status.last_error)
        self.assertEqual(status.discovered_tool_count, 0)
        self.assertEqual(status.endpoint, DEFAULT_URL)
        self.assertEqual(captured, {})
        with self.assertRaises(KeyError):
            gateway.get("mcp.n8n.search_workflows")

    def test_enabled_but_secret_missing(self) -> None:
        captured: dict[str, Any] = {}
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=True),
            environ={},
            client_factory=_factory(FakeMCP(), captured),
        )
        with self.assertLogs("atlas_core.integrations.n8n_mcp", level="WARNING") as logs:
            status = provider.connect(ToolGateway())
        self.assertTrue(status.configured)
        self.assertTrue(status.enabled)
        self.assertFalse(status.available)
        self.assertIn(DEFAULT_SECRET_REF, status.last_error or "")
        self.assertIn("not configured", status.last_error or "")
        self.assertEqual(captured, {})
        self.assertTrue(any("unavailable" in line for line in logs.output))

    def test_successful_authenticated_discovery(self) -> None:
        captured: dict[str, Any] = {}
        client = FakeMCP()
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=True, timeout_sec=5, read_timeout_sec=9),
            environ={DEFAULT_SECRET_REF: TOKEN},
            client_factory=_factory(client, captured),
        )
        gateway = ToolGateway()
        status = provider.connect(gateway)
        self.assertTrue(status.available)
        self.assertIsNone(status.last_error)
        self.assertEqual(status.endpoint, DEFAULT_URL)
        self.assertEqual(status.discovered_tool_count, 1)
        self.assertEqual(provider.tool_ids, ("mcp.n8n.search_workflows",))
        self.assertEqual(captured["url"], DEFAULT_URL)
        self.assertEqual(captured["timeout_sec"], 5)
        self.assertEqual(captured["read_timeout_sec"], 9)

    def test_authorization_bearer_header_is_passed(self) -> None:
        captured: dict[str, Any] = {}
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=True),
            environ={DEFAULT_SECRET_REF: f"  {TOKEN}  "},
            client_factory=_factory(FakeMCP(), captured),
        )
        provider.connect(ToolGateway())
        self.assertEqual(captured["headers"], {"Authorization": f"Bearer {TOKEN}"})

    def test_authentication_failure_is_reported(self) -> None:
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=True),
            environ={DEFAULT_SECRET_REF: TOKEN},
            client_factory=_factory(
                FailingMCP(RuntimeError("Client error '401 Unauthorized' for url 'http://127.0.0.1:5678/mcp-server/http'"))
            ),
        )
        with self.assertLogs("atlas_core.integrations.n8n_mcp", level="WARNING"):
            status = provider.connect(ToolGateway())
        self.assertFalse(status.available)
        self.assertIn("401", status.last_error or "")
        self.assertNotIn(TOKEN, status.last_error or "")

    def test_n8n_unavailable_is_reported(self) -> None:
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=True),
            environ={DEFAULT_SECRET_REF: TOKEN},
            client_factory=_factory(FailingMCP(ConnectionError("Connection refused"))),
        )
        with self.assertLogs("atlas_core.integrations.n8n_mcp", level="WARNING"):
            status = provider.connect(ToolGateway())
        self.assertTrue(status.enabled)
        self.assertFalse(status.available)
        self.assertIn("Connection refused", status.last_error or "")

    def test_discovered_tools_register_through_bridge_and_gateway(self) -> None:
        client = FakeMCP()
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=True, server_name="atlas-n8n", tool_prefix="mcp.n8n"),
            environ={DEFAULT_SECRET_REF: TOKEN},
            client_factory=_factory(client),
        )
        gateway = ToolGateway()
        provider.connect(gateway)
        spec, _handler = gateway.get("mcp.n8n.search_workflows")
        self.assertEqual(spec.origin.type, "mcp")
        self.assertEqual(spec.origin.server, "atlas-n8n")
        self.assertEqual(spec.origin.tool_name, "search_workflows")
        self.assertEqual(spec.origin.transport, "streamable-http")
        result = gateway.invoke(
            "mcp.n8n.search_workflows",
            {"query": "morning"},
            authority_scope="read",
        )
        self.assertTrue(result.ok)
        self.assertEqual(client.calls, [("search_workflows", {"query": "morning"})])

    def test_secret_never_appears_in_diagnostics_or_status(self) -> None:
        captured: dict[str, Any] = {}
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=True, url="http://user:n8n-mcp-test-token-do-not-leak@127.0.0.1:5678/mcp-server/http"),
            environ={DEFAULT_SECRET_REF: TOKEN},
            client_factory=_factory(
                FailingMCP(RuntimeError(f"Authorization: Bearer {TOKEN} rejected")),
                captured,
            ),
        )
        with self.assertLogs("atlas_core.integrations.n8n_mcp", level="WARNING"):
            status = provider.connect(ToolGateway())
        dumped = json.dumps(status.as_dict()) + repr(provider) + (status.last_error or "")
        self.assertNotIn(TOKEN, dumped)
        self.assertNotIn(TOKEN, status.endpoint or "")
        self.assertNotIn("user:", status.endpoint or "")
        self.assertIn("[redacted-authorization]", status.last_error or "")
        self.assertNotIn("Authorization", json.dumps(status.as_dict()))
        self.assertNotIn("Bearer", json.dumps(status.as_dict()))

    def test_connect_does_not_block_atlas_when_n8n_is_down(self) -> None:
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=True),
            environ={DEFAULT_SECRET_REF: TOKEN},
            client_factory=_factory(FailingMCP(ConnectionError("n8n down"))),
        )
        with self.assertLogs("atlas_core.integrations.n8n_mcp", level="WARNING"):
            status = provider.connect(ToolGateway())
        self.assertFalse(status.available)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        runtime = build_work_runtime(db_path=Path(tmp.name) / "atlas.db")
        task = runtime.store.create_work(
            objective="Prove Atlas still starts",
            success_criteria=("The task exists",),
            authority_scope="read",
        )
        self.assertEqual(task.status, "planned")

    def test_from_example_config_file(self) -> None:
        root = Path(__file__).resolve().parents[1]
        provider = N8NMCPProvider.from_file(root / "config" / "n8n-mcp.example.json", environ={})
        self.assertTrue(provider.status.configured)
        self.assertFalse(provider.status.enabled)
        self.assertEqual(provider.config.url, DEFAULT_URL)
        self.assertEqual(provider.config.secret_ref, DEFAULT_SECRET_REF)

    def test_unconfigured_provider(self) -> None:
        provider = N8NMCPProvider.unconfigured()
        status = provider.connect(ToolGateway())
        self.assertFalse(status.configured)
        self.assertFalse(status.enabled)
        self.assertFalse(status.available)
        self.assertIsNone(status.endpoint)
        self.assertIsNone(status.last_error)


if __name__ == "__main__":
    unittest.main()
