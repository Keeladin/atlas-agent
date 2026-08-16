from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atlas_morning.replay import replay_export

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "export_excerpt.txt"
CONFIG = ROOT / "config" / "v1.json"


class ReplayHarnessTests(unittest.TestCase):
    def test_replays_fixture_one_file_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = replay_export(FIXTURE, tmp, config_path=CONFIG)
            self.assertGreaterEqual(result["days"], 2)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["ok"], result["days"])
            packs = list((Path(tmp) / "packs").glob("morning-*.md"))
            self.assertEqual(len(packs), result["days"])
            log = Path(tmp) / "replay-log.md"
            self.assertTrue(log.exists())
            text = log.read_text(encoding="utf-8")
            self.assertIn("Operational days with relevant reports", text)
            self.assertIn("| ok |", text)
