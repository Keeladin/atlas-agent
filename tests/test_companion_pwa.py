from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atlas_companion.server import CompanionApp


class FakeService:
    def tasks(self): return [{"id": "task_one", "status": "waiting", "objective": "test"}]
    def detail(self, task_id): return {"presentation": {"task_id": task_id}, "markdown": "# result"}
    def run(self, task_id): return self.detail(task_id)
    def cancel(self, task_id): return self.detail(task_id)
    def decide(self, approval_id, decision, note=None): return self.detail("task_one")
    def create_and_run(self, body): return self.detail("task_new")


class CompanionPwaTests(unittest.TestCase):
    def setUp(self): self.app = CompanionApp(FakeService(), ROOT / "atlas_companion" / "web")
    def call(self, method, path, body=b""):
        out = {}
        result = self.app({"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}, lambda status, headers: out.update(status=status, headers=headers))
        return out["status"], b"".join(result)
    def test_lists_and_details_tasks(self):
        status, body = self.call("GET", "/api/tasks"); self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)[0]["id"], "task_one")
        status, body = self.call("GET", "/api/tasks/task_one"); self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)["presentation"]["task_id"], "task_one")
    def test_routes_mutations_to_runtime_adapter(self):
        status, body = self.call("POST", "/api/tasks/task_one/run", b"{}"); self.assertEqual(status, "200 OK")
        status, body = self.call("POST", "/api/approvals/approval_one/approve", b'{"note":"yes"}'); self.assertEqual(status, "200 OK")
        status, body = self.call("POST", "/api/tasks", b'{"objective":"new","criteria":["done"]}'); self.assertEqual(status, "201 Created")
    def test_serves_shell_without_api(self):
        status, body = self.call("GET", "/"); self.assertEqual(status, "200 OK"); self.assertIn(b"Atlas Companion", body)


if __name__ == "__main__": unittest.main()
