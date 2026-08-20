from __future__ import annotations

import unittest
from pathlib import Path


ADVANCED_ROOT = Path(__file__).resolve().parents[1] / "atlas_core" / "advanced"

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
    "atlas_core.chat",
    "atlas_core.work",
    "CapabilityRegistry",
    "CapabilityExecutionProfile",
    "CapabilityRegistration",
    "CapabilityHandler",
    "CapabilityBinding",
    "CapabilityRequest",
    "TaskRuntime",
    "TaskStore",
    "WorkStore",
    "ToolGateway",
    "MCPToolBridge",
    "N8NMCPProvider",
    "build_runtime",
    "plan_and_create",
    "sqlite3",
)

FORBIDDEN_IMPLEMENTATION = (
    "n8n.",
    "mcp.",
    "send_email",
    "create_workflow_from_code",
    "publish_workflow",
)


class AdvancedDependencyTests(unittest.TestCase):
    def test_advanced_package_does_not_import_work_or_chat(self) -> None:
        files = sorted(ADVANCED_ROOT.glob("*.py"))
        self.assertTrue(files, "atlas_core/advanced/ has no Python files")
        for path in files:
            source = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN:
                with self.subTest(file=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_advanced_package_does_not_name_implementations(self) -> None:
        for path in sorted(ADVANCED_ROOT.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_IMPLEMENTATION:
                with self.subTest(file=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)
            self.assertNotIn("execute_workflow", source)
