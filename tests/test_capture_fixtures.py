from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "atlas_mobile" / "fixtures" / "golden.json"
MACHINES = ROOT / "atlas_mobile" / "data" / "machines.json"
USERS = ROOT / "atlas_mobile" / "data" / "users.json"


class CaptureFixtureShapeTests(unittest.TestCase):
    """Fixtures and directories are data. Rules live in the PWA JS."""

    def test_golden_json_is_well_formed(self) -> None:
        data = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertIn("cases", data)
        self.assertIn("assemble", data)
        self.assertGreaterEqual(len(data["cases"]), 10)
        ids = [case["id"] for case in data["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in data["cases"]:
            self.assertTrue("expect" in case or "expect_thread" in case)

    def test_user_directory_is_configurable(self) -> None:
        data = json.loads(USERS.read_text(encoding="utf-8"))
        self.assertEqual(data.get("type"), "atlas.user_directory")
        users = data["users"]
        self.assertGreaterEqual(len(users), 1)
        for user in users:
            for key in (
                "id",
                "display_name",
                "role",
                "status",
                "can_create_supervisor_reports",
            ):
                self.assertIn(key, user)
        authors = [
            user
            for user in users
            if user["status"] == "active" and user["can_create_supervisor_reports"]
        ]
        self.assertGreaterEqual(len(authors), 1)

    def test_machine_directory_is_configurable(self) -> None:
        data = json.loads(MACHINES.read_text(encoding="utf-8"))
        self.assertEqual(data.get("type"), "atlas.machine_directory")
        machines = data["machines"]
        self.assertGreaterEqual(len(machines), 1)
        for machine in machines:
            for key in (
                "id",
                "canonical_id",
                "display_name",
                "aliases",
                "status",
                "available_in_picker",
            ):
                self.assertIn(key, machine)
        aliased = [m for m in machines if m["aliases"]]
        self.assertGreaterEqual(len(aliased), 1)

    def test_golden_covers_contract_examples(self) -> None:
        data = json.loads(GOLDEN.read_text(encoding="utf-8"))
        ids = {case["id"] for case in data["cases"]}
        for required in (
            "timed-completed-running",
            "timed-incomplete-still-under-repair",
            "completed-not-tested",
            "no-times-standing-green",
            "missing-machine-state-red",
            "missing-job-outcome-red",
            "suspicious-1030-0010-orange",
            "suspicious-2200-2045-orange",
            "continue-required-when-thread-open",
            "continue-running-closes",
            "completed-running-without-continue-does-not-close",
            "same-machine-new-issue-does-not-close",
        ):
            self.assertIn(required, ids)
        self.assertIn("SOS", data["assemble"]["expect_absent"])
