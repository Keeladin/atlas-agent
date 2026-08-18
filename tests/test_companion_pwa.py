from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atlas_companion.server import CompanionApp
from atlas_companion import telemetry


class FakeService:
    def tasks(self): return [{"id": "task_one", "status": "waiting", "objective": "test"}]
    def detail(self, task_id): return {"presentation": {"task_id": task_id}, "markdown": "# result"}
    def run(self, task_id): return self.detail(task_id)
    def cancel(self, task_id): return self.detail(task_id)
    def decide(self, approval_id, decision, note=None): return self.detail("task_one")
    def create_and_run(self, body): return self.detail("task_new")
    def health(self): return {"atlas": {"healthy": True, "running_executions": 0}}


class CompanionPwaTests(unittest.TestCase):
    def setUp(self): self.app = CompanionApp(FakeService(), ROOT / "atlas_companion" / "web")
    def call(self, method, path, body=b""):
        out = {}
        result = self.app({"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}, lambda status, headers: out.update(status=status, headers=headers))
        return out["status"], b"".join(result)
    def test_lists_and_details_tasks(self):
        status, body = self.call("GET", "/api/tasks"); self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)[0]["id"], "task_one")
        status, body = self.call("GET", "/api/tasks/task_one"); self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)["presentation"]["task_id"], "task_one")
    def test_exposes_server_side_health_shape(self):
        status, body = self.call("GET", "/api/health")
        self.assertEqual(status, "200 OK"); self.assertTrue(json.loads(body)["atlas"]["healthy"])
    def test_routes_mutations_to_runtime_adapter(self):
        status, body = self.call("POST", "/api/tasks/task_one/run", b"{}"); self.assertEqual(status, "200 OK")
        status, body = self.call("POST", "/api/approvals/approval_one/approve", b'{"note":"yes"}'); self.assertEqual(status, "200 OK")
        status, body = self.call("POST", "/api/tasks", b'{"objective":"new","criteria":["done"]}'); self.assertEqual(status, "201 Created")
    def test_serves_shell_without_api(self):
        status, body = self.call("GET", "/"); self.assertEqual(status, "200 OK"); self.assertIn(b"Atlas Companion", body)


class TelemetryParsingTests(unittest.TestCase):
    def test_parses_gpu_and_process_rows(self):
        original = telemetry._command
        replies = iter(["NVIDIA GeForce RTX 5070, 0, 6986, 12227, 49, 18, 250, P8", "31799, /app/llama-server, 6968"])
        telemetry._command = lambda *args: next(replies)
        try:
            gpu = telemetry._gpu()
        finally:
            telemetry._command = original
        self.assertTrue(gpu["available"]); self.assertEqual(gpu["name"], "NVIDIA GeForce RTX 5070")
        self.assertEqual(gpu["processes"][0]["name"], "/app/llama-server")


if __name__ == "__main__": unittest.main()
