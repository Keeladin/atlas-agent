from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT_ROOT = ROOT / "atlas_core" / "chat"
ADVANCED_ROOT = ROOT / "atlas_core" / "advanced"
WORK_ROOT = ROOT / "atlas_core" / "work"

CHAT_AND_ADVANCED_FORBIDDEN = (
    "atlas_core.work",
    "atlas_core.tasks",
    "atlas_core.runtime",
    "atlas_core.tools",
)

WORK_FORBIDDEN = (
    "atlas_core.chat",
    "atlas_companion",
    "atlas_core.bootstrap",
    "atlas_core.planner",
    "atlas_core.integrations",
    "atlas_core.mcp_http",
    "ChatRuntime",
    "AdvancedRuntime",
    "N8NMCPProvider",
    "n8n",
    "TaskRuntime",
    "CapabilityRegistry",
    "TaskPlanner",
    "RuntimeFrame",
)


def _python_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


class WorkDependencyTests(unittest.TestCase):
    def test_chat_cannot_import_work(self) -> None:
        files = _python_files(CHAT_ROOT)
        self.assertTrue(files)
        for path in files:
            source = path.read_text(encoding="utf-8")
            for forbidden in CHAT_AND_ADVANCED_FORBIDDEN:
                with self.subTest(file=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_advanced_cannot_import_work(self) -> None:
        files = _python_files(ADVANCED_ROOT)
        self.assertTrue(files)
        for path in files:
            source = path.read_text(encoding="utf-8")
            for forbidden in CHAT_AND_ADVANCED_FORBIDDEN:
                with self.subTest(file=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_work_does_not_import_chat_or_n8n(self) -> None:
        files = _python_files(WORK_ROOT)
        self.assertTrue(files)
        for path in files:
            source = path.read_text(encoding="utf-8")
            for forbidden in WORK_FORBIDDEN:
                with self.subTest(file=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)
