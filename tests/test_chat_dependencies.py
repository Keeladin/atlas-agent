from __future__ import annotations

import unittest
from pathlib import Path


CHAT_ROOT = Path(__file__).resolve().parents[1] / "atlas_core" / "chat"

FORBIDDEN = (
    "atlas_core.tasks",
    "atlas_core.runtime",
    "atlas_core.runtime_execution",
    "atlas_core.runtime_lifecycle",
    "atlas_core.runtime_finish",
    "atlas_core.bootstrap",
    "atlas_core.planner",
    "atlas_core.tools",
    "atlas_core.mcp_http",
    "atlas_core.integrations",
    "atlas_core.knowledge",
    "atlas_core.context",
    "atlas_core.work",
    "CapabilityRegistry",
    "CapabilityExecutionProfile",
    "CapabilityRegistration",
    "CapabilityHandler",
    "CapabilityRequest",
    "TaskRuntime",
    "TaskStore",
    "WorkStore",
    "ToolGateway",
    "MCPToolBridge",
    "N8NMCPProvider",
    "build_runtime",
)

FORBIDDEN_VENDOR = (
    "execute_workflow",
    "n8n",
    "mcp.",
)


class ChatDependencyTests(unittest.TestCase):
    def test_chat_package_does_not_import_work(self) -> None:
        files = sorted(CHAT_ROOT.glob("*.py"))
        self.assertTrue(files, "atlas_core/chat/ has no Python files")
        for path in files:
            source = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN:
                with self.subTest(file=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_chat_package_does_not_name_vendor_tools(self) -> None:
        for path in sorted(CHAT_ROOT.glob("*.py")):
            source = path.read_text(encoding="utf-8").casefold()
            for forbidden in FORBIDDEN_VENDOR:
                with self.subTest(file=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)
