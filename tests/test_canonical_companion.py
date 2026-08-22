from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
ARCHITECTURE = (
    ROOT / "Atlas Architecture — Runtime and Topology.md"
).read_text(encoding="utf-8")


class CanonicalCompanionDocsTests(unittest.TestCase):
    def test_readme_advertises_atlas_api_not_legacy_server(self) -> None:
        self.assertIn("python -m atlas_api", README)
        self.assertIn("companion/", README)
        self.assertIn("signed session cookie + CSRF", README)
        self.assertIn("Caddy", README)
        self.assertNotIn("atlas_core/tasks/", README)
        self.assertNotIn("--db instance/atlas.db tasks", README)
        self.assertNotIn("until Companion is rebuilt", README)
        self.assertIn("atlas_companion.server", README)
        self.assertIn("legacy", README.casefold())
        self.assertNotIn("uv run python -m atlas_companion.server", README)

    def test_architecture_does_not_call_companion_disconnected(self) -> None:
        self.assertNotIn("disconnected until rebuilt", ARCHITECTURE.casefold())
        self.assertNotIn("disconnected/dark until rebuilt", ARCHITECTURE)
        self.assertIn("atlas_api", ARCHITECTURE)
        self.assertIn("companion/", ARCHITECTURE)
