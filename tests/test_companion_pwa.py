from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atlas_companion.cloud_providers import ProviderStateStore
from atlas_companion.intent import preview_intent
from atlas_companion.local_models import LocalModelError, LocalModelManager
from atlas_companion.server import CompanionApp, CompanionService
from atlas_companion import telemetry


class FakeService:
    def tasks(self): return [{"id": "task_one", "status": "waiting", "objective": "test"}]
    def detail(self, task_id): return {"presentation": {"task_id": task_id}, "markdown": "# result"}
    def run(self, task_id): return self.detail(task_id)
    def cancel(self, task_id): return self.detail(task_id)
    def decide(self, approval_id, decision, note=None): return self.detail("task_one")
    def create_and_run(self, body): return self.detail("task_new")
    def preview_task(self, body): return {"objective": body.get("objective"), "criteria": ["inferred"], "authority": "interpret"}
    def ask(self, body):
        return {
            "message": body.get("message"),
            "reply": "ok",
            "work": self.detail("task_new"),
            "conversation_id": "conversation_default",
            "conversation": self.conversation("conversation_default"),
        }
    def delete_task(self, task_id, *, confirm_id=None): return {"deleted": task_id}
    def list_conversations(self): return [{"id": "conversation_default", "title": "Ask", "turn_count": 0}]
    def conversation(self, conversation_id):
        cid = "conversation_default" if conversation_id in {"", "current", None} else conversation_id
        return {"id": cid, "title": "Ask", "turn_count": 0, "turns": []}
    def cloud_providers(self): return [{"key": "xai:expert", "configured": False, "manageable": True}]
    def save_cloud_credential(self, provider_key, api_key): return {"key": provider_key, "configured": True}
    def delete_cloud_credential(self, provider_key): return {"key": provider_key, "configured": False}
    def verify_cloud_provider(self, provider_key): return {"key": provider_key, "verified": True}
    def refresh_cloud_models(self, provider_key): return {"key": provider_key, "discovered_models": ["grok-4.6"]}
    def select_cloud_model(self, provider_key, model): return {"key": provider_key, "selected_model": model}
    def enable_cloud_provider(self, provider_key, enabled): return {"key": provider_key, "enabled": enabled}
    def list_local_models(self): return {"slots": [{"id": "local:resident", "status": "loaded"}], "gpu": {}, "unmapped_gguf": []}
    def load_local_model(self, slot_id): return {"id": slot_id, "status": "loaded"}
    def unload_local_model(self, slot_id): return {"id": slot_id, "status": "stopped"}
    def activate_local_model(self, slot_id): return {"id": slot_id, "status": "loaded"}
    def health(self): return {"atlas": {"healthy": True, "running_executions": 0}}
    def approvals(self): return [{"id": "approval_one", "task_id": "task_one", "requested_action": "ingest"}]
    def documents(self): return [{"id": "doc_one", "title": "README.md", "chunk_count": 2}]
    def search_knowledge(self, query, limit=8): return {"query": query, "results": [{"title": "README.md", "text": query}]}
    def stat_source(self, path): return {"path": path, "title": "README.md", "byte_size": 12, "content_sha256": "abc"}
    def ingest(self, body): return self.detail("task_ingest")
    def search_task(self, body): return self.detail("task_search")


class CompanionPwaTests(unittest.TestCase):
    def setUp(self): self.app = CompanionApp(FakeService(), ROOT / "atlas_companion" / "web")
    def call(self, method, path, body=b""):
        out = {}
        result = self.app({"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(body)), "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "", "wsgi.input": io.BytesIO(body)}, lambda status, headers: out.update(status=status, headers=headers))
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
        status, body = self.call("POST", "/api/tasks/preview", b'{"objective":"what is ohms law"}'); self.assertEqual(status, "200 OK")
        status, body = self.call("POST", "/api/ask", b'{"message":"hello"}'); self.assertEqual(status, "201 Created")
        self.assertEqual(json.loads(body)["conversation_id"], "conversation_default")
        status, body = self.call("GET", "/api/conversations"); self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)[0]["id"], "conversation_default")
        status, body = self.call("GET", "/api/conversations/current"); self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)["id"], "conversation_default")
        status, body = self.call("DELETE", "/api/tasks/task_one", b'{"confirm_id":"task_one"}'); self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)["deleted"], "task_one")
        status, body = self.call("GET", "/api/models/cloud"); self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)[0]["key"], "xai:expert")
        status, body = self.call("POST", "/api/models/cloud/xai:expert/select", b'{"model":"grok-4.6"}'); self.assertEqual(status, "200 OK")
        status, body = self.call("GET", "/api/models/local"); self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)["slots"][0]["id"], "local:resident")
    def test_serves_shell_without_api(self):
        status, body = self.call("GET", "/"); self.assertEqual(status, "200 OK"); self.assertIn(b"Atlas Companion", body)
        self.assertIn(b"Ask Atlas", body); self.assertIn(b"What should Atlas do?", body)
        self.assertIn(b"ask-thread", body)
        self.assertIn(b'data-work-view="recurring"', body)
        self.assertIn(b"personal-screen", body)
        self.assertIn(b"not connected yet", body)
        self.assertIn(b"Index a text source", body)
        self.assertIn(b"cloud-providers", body)
        self.assertIn(b"local-models", body)
        self.assertIn(b'data-knowledge-view="library"', body)
        self.assertIn(b'data-knowledge-view="search"', body)
        self.assertIn(b'data-knowledge-view="indexing"', body)
        self.assertIn(b"Personal", body); self.assertIn(b"Models", body); self.assertIn(b"Settings", body)
        self.assertNotIn(b"Operational picture", body)
        self.assertNotIn(b">Today</span>", body)
        self.assertIn(b"ask-screen", body)
        self.assertIn(b'data-screen="ask"', body)
    def test_routes_knowledge_and_approvals(self):
        status, body = self.call("GET", "/api/approvals")
        self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)[0]["id"], "approval_one")
        status, body = self.call("GET", "/api/knowledge/documents")
        self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)[0]["title"], "README.md")
        status, body = self.call("GET", "/api/knowledge/search?q=ContextBuilder")
        self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)["query"], "ContextBuilder")
        status, body = self.call("GET", "/api/knowledge/stat?path=/tmp/readme.md")
        self.assertEqual(status, "200 OK"); self.assertEqual(json.loads(body)["byte_size"], 12)
        status, body = self.call("POST", "/api/knowledge/ingest", b'{"source_path":"/tmp/readme.md"}')
        self.assertEqual(status, "201 Created"); self.assertEqual(json.loads(body)["presentation"]["task_id"], "task_ingest")


class CompanionKnowledgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas.db"
        self.source = Path(self.tmp.name) / "note.md"
        self.source.write_text("ContextBuilder assembles a bounded execution projection.\n", encoding="utf-8")
        self.service = CompanionService(db_path=self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_ingest_and_search_use_runtime_not_title_filter(self):
        stat = self.service.stat_source(str(self.source))
        self.assertEqual(stat["title"], "note.md")
        self.assertGreater(stat["byte_size"], 0)
        result = self.service.ingest({"source_path": str(self.source)})
        self.assertEqual(result["presentation"]["status"], "completed")
        docs = self.service.documents()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["title"], "note.md")
        hits = self.service.search_knowledge("ContextBuilder")
        self.assertTrue(hits["results"])
        self.assertIn("ContextBuilder", hits["results"][0]["text"])
        request = next(
            artifact
            for artifact in result["snapshot"]["artifacts"]
            if artifact["kind"] == "knowledge_ingest_request"
        )
        self.assertNotIn("text", request["payload"])
        self.assertEqual(request["payload"]["source_path"], stat["path"])

    def test_cancel_and_run_reject_terminal_tasks(self):
        result = self.service.ingest({"source_path": str(self.source)})
        task_id = result["presentation"]["task_id"]
        with self.assertRaises(Exception) as cancel_error:
            self.service.cancel(task_id)
        self.assertIn("already completed", str(cancel_error.exception))
        with self.assertRaises(Exception) as run_error:
            self.service.run(task_id)
        self.assertIn("already completed", str(run_error.exception))

    def test_failed_task_exposes_reason(self):
        missing = Path(self.tmp.name) / "gone.md"
        store = self.service.runtime.store
        task = store.create_task(
            objective="Index missing source",
            success_criteria=("The source is durably indexed with chunk provenance.",),
            authority_scope="modify_internal",
        )
        request = store.put_artifact(
            task.id,
            kind="knowledge_ingest_request",
            payload={"title": "gone.md", "source_path": str(missing), "source_uri": str(missing)},
        )
        store.add_step(
            task.id,
            description="Chunk and index extracted text.",
            capability="knowledge.ingest_text",
            capability_version=self.service.runtime.capabilities.get("knowledge.ingest_text").spec.version,
            input_artifact_ids=(request.id,),
            metadata={"accept_all_criteria": True},
        )
        self.service.runtime.run_until_blocked(task.id)
        listed = next(item for item in self.service.tasks() if item["id"] == task.id)
        detail = self.service.detail(task.id)
        self.assertEqual(listed["status"], "failed")
        self.assertIn("missing", listed["failure_reason"])
        self.assertEqual(detail["presentation"]["failure_reason"], listed["failure_reason"])
        self.assertIn("**Failure:**", detail["markdown"])
        self.assertLess(detail["markdown"].index("**Failure:**"), detail["markdown"].index("## Success criteria"))

    def test_ask_search_bypasses_planner_and_includes_query(self):
        self.service.ingest({"source_path": str(self.source)})
        result = self.service.create_and_run(
            {"objective": "Search Atlas knowledge for: ContextBuilder", "criteria": ["hits"]}
        )
        self.assertEqual(result["presentation"]["status"], "completed")
        request = next(
            artifact
            for artifact in result["snapshot"]["artifacts"]
            if artifact["kind"] == "knowledge_search_request"
        )
        self.assertEqual(request["payload"]["query"], "ContextBuilder")

    def test_ask_ingest_bypasses_planner_and_includes_title(self):
        result = self.service.create_and_run(
            {"objective": f"Index local knowledge source {self.source}", "criteria": ["indexed"]}
        )
        self.assertEqual(result["presentation"]["status"], "completed")
        request = next(
            artifact
            for artifact in result["snapshot"]["artifacts"]
            if artifact["kind"] == "knowledge_ingest_request"
        )
        self.assertEqual(request["payload"]["title"], "note.md")
        self.assertEqual(request["payload"]["source_path"], str(self.source.resolve()))


def _write_provider_overlay(path: Path, *, key: str, model: str, enabled: bool = True, extra: dict | None = None) -> Path:
    payload = {
        "providers": {
            key: {
                "kind": "openai_compatible_chat",
                "model": model,
                "base_url": "http://127.0.0.1:1234",
                "local": True,
                "enabled": enabled,
                "capabilities": {"planning.general": 0.5, "reasoning.general": 0.4, "generation.compose": 0.6},
                "api_key": "xai-SECRETVALUE-do-not-leak",
                "api_key_env": "XAI_API_KEY",
                **(extra or {}),
            },
            "cloud:disabled": {
                "kind": "openai_compatible_chat",
                "model": "hidden-model",
                "base_url": "https://api.example.invalid",
                "local": False,
                "enabled": False,
                "capabilities": {"planning.general": 0.9},
                "api_key": "sk-OTHERSECRET-do-not-leak",
            },
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class CompanionIntentTests(unittest.TestCase):
    def test_preview_infers_story_criteria_and_interpret_authority(self):
        intent = preview_intent(
            "Tell me a believable short story about a man who finds a pond of immortality."
        )
        self.assertEqual(intent.authority, "interpret")
        self.assertTrue(intent.inferred_criteria)
        self.assertTrue(intent.inferred_authority)
        self.assertEqual(intent.deliverable_kind, "narrative")
        joined = " ".join(intent.criteria).casefold()
        self.assertIn("short story", joined)
        self.assertIn("believable", joined)

    def test_preview_ingest_uses_modify_internal(self):
        intent = preview_intent("Index local knowledge source /tmp/manual.md")
        self.assertEqual(intent.authority, "modify_internal")
        self.assertEqual(intent.workflow, "knowledge_ingest")

    def test_supplied_criteria_win(self):
        intent = preview_intent("Do the job", criteria=["Must cite sources"])
        self.assertEqual(intent.criteria, ("Must cite sources",))
        self.assertFalse(intent.inferred_criteria)


class CompanionTaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas.db"
        self.source = Path(self.tmp.name) / "note.md"
        self.source.write_text("ContextBuilder assembles a bounded execution projection.\n", encoding="utf-8")
        self.service = CompanionService(db_path=self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_and_create_without_user_criteria(self):
        preview = self.service.preview_task({"objective": "what is ohms law"})
        self.assertEqual(preview["authority"], "interpret")
        self.assertTrue(preview["criteria"])
        result = self.service.ingest({"source_path": str(self.source)})
        task_id = result["presentation"]["task_id"]
        listed = next(item for item in self.service.tasks() if item["id"] == task_id)
        self.assertEqual(listed["status"], "completed")
        self.assertEqual(listed["workflow"], "knowledge_ingest")
        self.assertEqual(listed["metadata"]["workflow"], "knowledge_ingest")
        created = self.service.create_and_run({"objective": f"Index local knowledge source {self.source}"})
        self.assertEqual(created["presentation"]["status"], "completed")

    def test_delete_removes_task_owned_rows_and_keeps_knowledge(self):
        result = self.service.ingest({"source_path": str(self.source)})
        task_id = result["presentation"]["task_id"]
        self.assertTrue(self.service.documents())
        with self.assertRaises(ValueError):
            self.service.delete_task(task_id, confirm_id="wrong")
        deleted = self.service.delete_task(task_id, confirm_id=task_id)
        self.assertEqual(deleted["deleted"], task_id)
        self.assertFalse(any(item["id"] == task_id for item in self.service.tasks()))
        self.assertTrue(self.service.documents())
        store = self.service.runtime.store
        with store._db() as db:
            for table in store._TASK_OWNED_TABLES:
                if table == "tasks":
                    n = db.execute("SELECT COUNT(*) AS n FROM tasks WHERE id=?", (task_id,)).fetchone()["n"]
                else:
                    n = db.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE task_id=?", (task_id,)).fetchone()["n"]
                self.assertEqual(int(n), 0, table)

    def test_recurring_workflows_are_exposed_on_task_list(self):
        store = self.service.runtime.store
        morning = store.create_task(
            objective="Assemble the morning pack",
            success_criteria=("The morning pack is assembled.",),
            authority_scope="read",
            metadata={"workflow": "morning_v1"},
        )
        ingest = self.service.ingest({"source_path": str(self.source)})
        listed = {item["id"]: item for item in self.service.tasks()}
        self.assertEqual(listed[morning.id]["workflow"], "morning_v1")
        self.assertEqual(listed[ingest["presentation"]["task_id"]]["workflow"], "knowledge_ingest")

    def test_cancel_recovers_running_execution(self):
        store = self.service.runtime.store
        task = store.create_task(
            objective="Interrupt me",
            success_criteria=("Done",),
            authority_scope="read",
        )
        step = store.add_step(task.id, description="Work", capability="demo.work")
        store.set_task_status(task.id, "active")
        running = store.begin_execution(task.id, step_id=step.id, capability="demo.work")
        self.service.cancel(task.id)
        self.assertEqual(store.get_task(task.id).status, "cancelled")
        self.assertNotEqual(store.get_execution(running.id).status, "running")


class CompanionConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas.db"
        self.source = Path(self.tmp.name) / "note.md"
        self.source.write_text("ContextBuilder assembles a bounded execution projection.\n", encoding="utf-8")
        self.service = CompanionService(db_path=self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_conversation_round_trip_and_survives_task_delete(self):
        empty = self.service.conversation("current")
        self.assertEqual(empty["id"], "conversation_default")
        self.assertEqual(empty["turns"], [])
        self.service.ingest({"source_path": str(self.source)})
        first = self.service.ask({"message": "Search Atlas knowledge for: ContextBuilder"})
        self.assertEqual(first["conversation_id"], "conversation_default")
        self.assertEqual(len(first["conversation"]["turns"]), 2)
        self.assertEqual(first["conversation"]["turns"][0]["role"], "user")
        self.assertEqual(first["conversation"]["turns"][0]["content"], "Search Atlas knowledge for: ContextBuilder")
        self.assertEqual(first["conversation"]["turns"][1]["role"], "atlas")
        task_id = first["work"]["presentation"]["task_id"]
        self.assertEqual(first["conversation"]["turns"][1]["task_id"], task_id)
        listed_task = next(item for item in self.service.tasks() if item["id"] == task_id)
        self.assertEqual(listed_task["metadata"]["conversation_id"], "conversation_default")
        self.assertEqual(listed_task["metadata"]["origin"], "ask")
        second = self.service.ask({"message": f"Index local knowledge source {self.source}", "conversation_id": first["conversation_id"]})
        loaded = self.service.conversation(first["conversation_id"])
        self.assertEqual(loaded["turn_count"], 4)
        self.assertEqual([turn["role"] for turn in loaded["turns"]], ["user", "atlas", "user", "atlas"])
        self.assertEqual(loaded["title"], "Search Atlas knowledge for: ContextBuilder")
        self.assertEqual(self.service.list_conversations()[0]["id"], "conversation_default")
        self.service.delete_task(task_id, confirm_id=task_id)
        after_delete = self.service.conversation(first["conversation_id"])
        self.assertEqual(after_delete["turns"][1]["task_id"], task_id)
        self.assertEqual(after_delete["turns"][1]["task_status"], "deleted")
        self.assertEqual(len(after_delete["turns"]), 4)

    def test_unknown_conversation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.conversation("conversation_missing")
        with self.assertRaises(ValueError):
            self.service.ask({"message": "hello", "conversation_id": "conversation_missing"})


class CompanionCloudModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas.db"
        self.overlay = Path(self.tmp.name) / "providers.json"
        self.overlay.write_text(
            json.dumps(
                {
                    "providers": {
                        "xai:expert": {
                            "kind": "openai_compatible_chat",
                            "model": "grok-4.5",
                            "base_url": "https://api.x.ai",
                            "api_key_env": "XAI_API_KEY",
                            "enabled": False,
                            "local": False,
                            "capabilities": {
                                "planning.general": 0.9,
                                "reasoning.general": 0.9,
                                "generation.compose": 0.9,
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.service = CompanionService(db_path=self.db, provider_config=self.overlay)
        self._prior_key = os.environ.pop("XAI_API_KEY", None)

    def tearDown(self):
        if self._prior_key is None:
            os.environ.pop("XAI_API_KEY", None)
        else:
            os.environ["XAI_API_KEY"] = self._prior_key
        self.tmp.cleanup()

    def test_enable_without_key_fails(self):
        self.assertFalse(self.service.cloud_providers()[0]["configured"])
        with self.assertRaises(ValueError):
            self.service.enable_cloud_provider("xai:expert", True)

    @patch("atlas_companion.server.xai_list_models", return_value=["grok-4.3", "grok-4.5", "grok-4.6"])
    def test_save_verify_select_enable_without_leaking_secret(self, _mocked):
        secret = "xai-SECRETVALUE-do-not-leak"
        saved = self.service.save_cloud_credential("xai:expert", secret)
        self.assertTrue(saved["configured"])
        self.assertEqual(saved["source"], "secret_file")
        self.assertEqual(saved["discovered_models"], ["grok-4.3", "grok-4.5", "grok-4.6"])
        self.assertNotIn(secret, json.dumps(saved))
        self.assertNotIn(secret, json.dumps(self.service.health()))
        self.assertNotIn(secret, self.overlay.read_text(encoding="utf-8"))
        selected = self.service.select_cloud_model("xai:expert", "grok-4.6")
        self.assertEqual(selected["selected_model"], "grok-4.6")
        again = self.service.select_cloud_model("xai:expert", "grok-4.5")
        self.assertEqual(again["selected_model"], "grok-4.5")
        enabled = self.service.enable_cloud_provider("xai:expert", True)
        self.assertTrue(enabled["enabled"])
        live = self.service.health()["runtime"]["providers"]
        self.assertEqual([item["key"] for item in live], ["xai:expert"])
        self.assertEqual(live[0]["model"], "grok-4.5")
        self.service.enable_cloud_provider("xai:expert", False)
        self.assertTrue(self.service.credentials.configured("xai:expert"))
        self.assertFalse(self.service.cloud_providers()[0]["enabled"])
        mode = self.service.credentials.path_for("xai:expert").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    @patch("atlas_companion.server.xai_list_models", return_value=["grok-4.6", "grok-4.20-0309-reasoning"])
    def test_enable_cloud_is_exclusive_and_routable_for_planning(self, _mocked):
        overlay = Path(self.tmp.name) / "both.json"
        overlay.write_text(
            json.dumps(
                {
                    "providers": {
                        "local:resident": {
                            "kind": "openai_compatible_chat",
                            "model": "atlas",
                            "base_url": "http://127.0.0.1:1234",
                            "local": True,
                            "enabled": True,
                            "capabilities": {
                                "planning.general": 0.5,
                                "reasoning.general": 0.5,
                                "generation.compose": 0.5,
                            },
                        },
                        "xai:expert": {
                            "kind": "openai_compatible_chat",
                            "model": "grok-4.5",
                            "base_url": "https://api.x.ai",
                            "api_key_env": "XAI_API_KEY",
                            "enabled": False,
                            "local": False,
                            "capabilities": {
                                "planning.general": 0.5,
                                "reasoning.general": 0.5,
                                "generation.compose": 0.5,
                            },
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        service = CompanionService(db_path=Path(self.tmp.name) / "both.db", provider_config=overlay)
        service.credentials.put("xai:expert", "xai-SECRETVALUE-do-not-leak")
        service.save_cloud_credential("xai:expert", "xai-SECRETVALUE-do-not-leak")
        service.select_cloud_model("xai:expert", "grok-4.20-0309-reasoning")
        before = [item["key"] for item in service.health()["runtime"]["providers"]]
        self.assertEqual(before, ["local:resident"])
        enabled = service.enable_cloud_provider("xai:expert", True)
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["selected_model"], "grok-4.20-0309-reasoning")
        live = service.health()["runtime"]["providers"]
        self.assertEqual([item["key"] for item in live], ["xai:expert"])
        self.assertEqual(live[0]["model"], "grok-4.20-0309-reasoning")
        self.assertIn("planning.general", live[0]["scores"])
        self.assertFalse(service.provider_state.get("local:resident").get("enabled"))
        planning = service.runtime.capabilities.get("planning.general").spec
        decision = service.runtime.model_router.select(planning, context_chars=100)
        self.assertEqual(decision.provider.spec.key, "xai:expert")
        self.assertEqual(decision.provider.spec.model, "grok-4.20-0309-reasoning")
        service.enable_cloud_provider("xai:expert", False)
        restored = [item["key"] for item in service.health()["runtime"]["providers"]]
        self.assertEqual(restored, ["local:resident"])


class FakeInferenceHost:
    def __init__(self):
        self.models_root = Path("/nonexistent-models")
        self.commands = []
        self.healthy = {"http://127.0.0.1:1234": True, "http://127.0.0.1:1235": False}
        self.states = {
            "atlas-inference": {"exists": True, "running": True, "status": "running", "health": "healthy", "exit_code": 0},
            "atlas-inference-heavy": {"exists": True, "running": False, "status": "exited", "health": None, "exit_code": 0},
        }

    def container_state(self, name):
        return dict(self.states.get(name) or {"exists": False, "running": False, "status": "missing", "health": None, "exit_code": None})

    def compose(self, *args):
        self.commands.append(args)
        service = args[-1]
        container = "atlas-inference-heavy" if "heavy" in service else "atlas-inference"
        endpoint = "http://127.0.0.1:1235" if "heavy" in service else "http://127.0.0.1:1234"
        if args[0] == "stop":
            self.states[container] = {"exists": True, "running": False, "status": "exited", "health": None, "exit_code": 0}
            self.healthy[endpoint] = False
        elif args[0] in {"start", "up"}:
            self.states[container] = {"exists": True, "running": True, "status": "running", "health": "healthy", "exit_code": 0}
            self.healthy[endpoint] = True

    def endpoint_healthy(self, url):
        return bool(self.healthy.get(url))

    def gpu_memory(self):
        return 6976, 12227

    def list_gguf(self):
        return []


class CompanionLocalModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.host = FakeInferenceHost()
        self.state = ProviderStateStore(Path(self.tmp.name))
        self.reloads = []
        self.manager = LocalModelManager(
            host=self.host,
            state=self.state,
            reload_router=lambda: self.reloads.append("reload"),
            wait=lambda: None,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_includes_resident_and_heavy(self):
        catalog = self.manager.catalog()
        ids = [slot["id"] for slot in catalog["slots"]]
        self.assertEqual(ids, ["local:resident", "local:heavy"])
        by_id = {slot["id"]: slot for slot in catalog["slots"]}
        self.assertEqual(by_id["local:resident"]["status"], "loaded")
        self.assertEqual(by_id["local:heavy"]["status"], "stopped")
        self.assertEqual(by_id["local:resident"]["quantization"], "Q6_K")
        self.assertEqual(by_id["local:heavy"]["quantization"], "Q4_K_M")

    def test_load_refuses_second_gpu_model(self):
        with self.assertRaises(LocalModelError) as raised:
            self.manager.load("local:heavy")
        self.assertIn("sequentially", str(raised.exception))
        self.assertFalse(self.host.commands)

    def test_activate_heavy_stops_resident(self):
        result = self.manager.activate("local:heavy")
        self.assertEqual(result["status"], "loaded")
        self.assertIn(("stop", "atlas-inference"), self.host.commands)
        self.assertTrue(any(cmd[0] in {"start", "up"} and cmd[-1] == "atlas-inference-heavy" for cmd in self.host.commands))
        self.assertFalse(self.host.states["atlas-inference"]["running"])
        self.assertTrue(self.host.states["atlas-inference-heavy"]["running"])
        self.assertTrue(self.state.get("local:heavy").get("enabled"))
        self.assertFalse(self.state.get("local:resident").get("enabled"))
        self.assertEqual(self.state.get("local:heavy").get("base_url"), "http://127.0.0.1:1235")
        self.assertEqual(self.reloads, ["reload"])


class CompanionRuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_health_exposes_assembler_start_time_and_provider_identity(self):
        overlay = _write_provider_overlay(Path(self.tmp.name) / "cell.json", key="local:resident", model="atlas")
        service = CompanionService(db_path=self.db, provider_config=overlay)
        health = service.health()
        runtime = health["runtime"]
        self.assertEqual(runtime["assembler_version"], "2.2.0")
        self.assertTrue(runtime["started_at"])
        self.assertIn("T", runtime["started_at"])
        self.assertEqual(runtime["pid"], os.getpid())
        self.assertEqual(Path(runtime["provider_config"]), overlay.resolve())
        keys = [item["key"] for item in runtime["providers"]]
        self.assertEqual(keys, ["local:resident"])
        resident = runtime["providers"][0]
        self.assertEqual(resident["model"], "atlas")
        self.assertEqual(resident["scores"]["planning.general"], 0.5)
        self.assertEqual(resident["scores"]["generation.compose"], 0.6)
        self.assertIn("cloud:disabled", runtime["disabled_provider_keys"])
        self.assertNotIn("cloud:disabled", keys)
        self.assertNotIn("hidden-model", json.dumps(health))

    def test_health_omits_secrets_from_provider_files(self):
        overlay = _write_provider_overlay(Path(self.tmp.name) / "secret.json", key="xai:expert", model="grok-4.6")
        health = CompanionService(db_path=self.db, provider_config=overlay).health()
        dumped = json.dumps(health)
        self.assertNotIn("xai-SECRETVALUE-do-not-leak", dumped)
        self.assertNotIn("sk-OTHERSECRET-do-not-leak", dumped)
        self.assertNotIn("XAI_API_KEY", dumped)
        self.assertNotIn("api_key", dumped)

    def test_health_reflects_a_new_provider_overlay(self):
        first = _write_provider_overlay(Path(self.tmp.name) / "a.json", key="local:resident", model="atlas")
        second = _write_provider_overlay(Path(self.tmp.name) / "b.json", key="local:heavy", model="atlas-heavy")
        health_a = CompanionService(db_path=self.db, provider_config=first).health()["runtime"]
        health_b = CompanionService(db_path=Path(self.tmp.name) / "atlas-b.db", provider_config=second).health()["runtime"]
        self.assertEqual(health_a["providers"][0]["key"], "local:resident")
        self.assertEqual(health_a["providers"][0]["model"], "atlas")
        self.assertEqual(health_b["providers"][0]["key"], "local:heavy")
        self.assertEqual(health_b["providers"][0]["model"], "atlas-heavy")
        self.assertEqual(Path(health_a["provider_config"]), first.resolve())
        self.assertEqual(Path(health_b["provider_config"]), second.resolve())
        self.assertNotEqual(health_a["started_at"], health_b["started_at"])


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


if __name__ == "__main__":
    unittest.main()
