from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from atlas_api.app import create_app, default_listen
from atlas_api.auth import AuthService
from atlas_api.compose import DEFAULT_HOST, DEFAULT_PORT, compose_services
from atlas_api.views.work import build_work_detail
from atlas_core.advanced import AdvancedRuntime, TaskBrief
from atlas_core.capabilities import CapabilityOutcome
from atlas_core.capabilities.awareness import brief_catalog
from atlas_core.chat import ChatRuntime
from atlas_core.chat.conversations import ConversationStore
from atlas_core.providers import ModelResponse, ProviderSpec
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    build_work_runtime,
)
from atlas_core.work.control import is_paused


class FakeProvider:
    def __init__(self, text: str) -> None:
        self.spec = ProviderSpec(
            key="fake:local",
            model="fake",
            provider_kind="fake",
            capabilities={"conversation.reply": 1.0, "advanced.brief": 1.0},
            local=True,
            enabled=True,
        )
        self.text = text
        self.calls: list = []

    def generate(self, request):
        self.calls.append(request)
        return ModelResponse(self.text, self.spec.key, self.spec.model, raw={})


def _pass_handler(_request):
    return CapabilityOutcome(
        "pass",
        output={"ok": True},
        receipt={"ok": True},
    )


class AtlasApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.work_db = root / "work.db"
        self.chat_db = root / "chat.db"
        from atlas_api.auth import CookiePolicy

        self.auth = AuthService(
            password="test-password",
            secret="test-session-secret-with-enough-entropy",
            cookie_policy=CookiePolicy(
                secure=False, samesite="lax", mode="explicit_insecure_dev"
            ),
        )
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                side_effects=("external_email",),
            ),
            _pass_handler,
        )
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.search",
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _pass_handler,
        )
        work = build_work_runtime(db_path=self.work_db, profiles=inventory)
        chat_provider = FakeProvider("Hello from Atlas chat.")
        conversations = ConversationStore(self.chat_db)
        conversations.initialize()
        chat = ChatRuntime(
            conversations=conversations,
            provider=chat_provider,
            awareness=(),
        )
        brief_json = json.dumps(
            {
                "objective": "Send the report",
                "capabilities": ["communication.email.send"],
                "expected_effect": "external communication",
                "constraints": [],
                "deliverable_kind": "communication",
                "notes": None,
            }
        )
        advanced = AdvancedRuntime(
            provider=FakeProvider(brief_json),
            catalog=brief_catalog(),
        )
        services = compose_services(
            work_db=self.work_db,
            chat_db=self.chat_db,
            auth=self.auth,
            chat=chat,
            advanced=advanced,
            work=work,
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
        )
        self.app = create_app(services=services, serve_companion=False)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _login(self) -> None:
        response = self.client.post(
            "/api/auth/login", json={"password": "test-password"}
        )
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json()["csrf_token"]

    def _headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}

    def test_health_and_listen_defaults(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["listen"]["host"], "127.0.0.1")
        self.assertEqual(body["listen"]["port"], 8080)
        self.assertEqual(default_listen(), (DEFAULT_HOST, DEFAULT_PORT))
        self.assertNotIn("0.0.0.0", json.dumps(body))
        from atlas_api.__main__ import main as api_main

        with self.assertRaises(SystemExit) as ctx:
            api_main(["--host", "0.0.0.0", "--port", "8080"])
        self.assertIn("127.0.0.1", str(ctx.exception))

    def test_no_ask_route(self) -> None:
        self._login()
        response = self.client.post(
            "/api/ask",
            json={"message": "hi"},
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 404)
        paths = [getattr(route, "path", "") for route in self.app.routes]
        self.assertTrue(all("/api/ask" not in path for path in paths))

    def test_unauthenticated_mutations_fail(self) -> None:
        response = self.client.post("/api/work", json={})
        self.assertEqual(response.status_code, 401)
        response = self.client.post("/api/advanced/brief", json={"objective": "x"})
        self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/api/chat/messages", json={"message": "hi"}
        )
        self.assertEqual(response.status_code, 401)

    def test_csrf_required_for_mutations(self) -> None:
        self._login()
        response = self.client.post("/api/advanced/brief", json={"objective": "Send email"})
        self.assertEqual(response.status_code, 403)

    def test_chat_advanced_work_roots(self) -> None:
        self._login()
        created = self.client.post(
            "/api/chat/conversations",
            json={"title": "Ops"},
            headers=self._headers(),
        )
        self.assertEqual(created.status_code, 201)
        conversation_id = created.json()["id"]
        message = self.client.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"message": "Hello"},
            headers=self._headers(),
        )
        self.assertEqual(message.status_code, 200)
        self.assertIn("Hello from Atlas chat", message.json()["reply"])

        brief = self.client.post(
            "/api/advanced/brief",
            json={"objective": "Send the weekly report"},
            headers=self._headers(),
        )
        self.assertEqual(brief.status_code, 200)
        self.assertEqual(brief.json().get("status"), "brief")
        self.assertEqual(
            brief.json()["capabilities"],
            ["communication.email.send"],
        )
        self.assertTrue(brief.json()["capabilities"])

        accepted = self.client.post(
            "/api/work",
            json={
                "brief": brief.json(),
                "authority_scope": "communicate",
                "inputs": {
                    "communication.email.send": {
                        "to": "ops@example.invalid",
                        "subject": "Weekly",
                    }
                },
            },
            headers=self._headers(),
        )
        self.assertEqual(accepted.status_code, 201)
        work_id = accepted.json()["work_id"]
        detail = accepted.json()
        self.assertIn("phase", detail)
        self.assertNotIn("success_criteria_json", detail)
        self.assertNotIn("metadata_json", detail)

        ran = self.client.post(
            f"/api/work/{work_id}/run",
            json={},
            headers=self._headers(),
        )
        self.assertEqual(ran.status_code, 200)
        detail = ran.json()["detail"]
        self.assertEqual(detail["phase"], "waiting_confirmation")
        self.assertEqual(detail["status"], "waiting")
        self.assertNotEqual(detail["phase"], "running")
        self.assertTrue(detail["pending_confirmations"])
        self.assertFalse(detail["pending_approvals"])
        self.assertEqual(
            detail["blocking"]["kind"],
            "payload_confirmation",
        )
        confirmation_id = detail["pending_confirmations"][0]["id"]
        self.assertIn(
            "Atlas wants to send this email",
            detail["pending_confirmations"][0]["summary"],
        )

        confirmed = self.client.post(
            f"/api/work/confirmations/{confirmation_id}/confirm",
            json={},
            headers=self._headers(),
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["kind"], "payload_confirmation")

        finished = self.client.post(
            f"/api/work/{work_id}/run",
            json={},
            headers=self._headers(),
        )
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(finished.json()["detail"]["status"], "completed")

    def test_zero_capability_output_never_returns_invalid_task_brief(self) -> None:
        self._login()
        objective = (
            "design a chatgpt style agent ui for my personal agent called atlas"
        )
        empty_json = json.dumps(
            {
                "objective": objective,
                "capabilities": [],
                "reason": (
                    "No briefable capability covers product or UI design work."
                ),
                "closest_capability": "coding.software_engineering",
            }
        )
        self.app.state.services.advanced = AdvancedRuntime(
            provider=FakeProvider(empty_json),
            catalog=brief_catalog(),
        )
        response = self.client.post(
            "/api/advanced/brief",
            json={"objective": objective},
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "unsupported")
        self.assertEqual(body["objective"], objective)
        self.assertIn("UI design", body["reason"])
        self.assertEqual(body["closest_capability"], "coding.software_engineering")
        self.assertNotIn("capabilities", body)
        self.assertNotIn("required_authority", body)
        self.assertNotIn("expected_effect", body)

        accepted = self.client.post(
            "/api/work",
            json={"brief": body, "authority_scope": "interpret"},
            headers=self._headers(),
        )
        self.assertEqual(accepted.status_code, 400)
        self.assertIn("unsupported", accepted.json()["error"].casefold())

        invalid = self.client.post(
            "/api/work",
            json={
                "brief": {
                    "objective": objective,
                    "capabilities": [],
                    "required_authority": "interpret",
                    "expected_effect": "design",
                },
                "authority_scope": "interpret",
            },
            headers=self._headers(),
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("capability", invalid.json()["error"].casefold())

    def test_authority_and_confirmation_are_separate_endpoints(self) -> None:
        self._login()
        paths = [getattr(route, "path", "") for route in self.app.routes]
        self.assertIn("/api/work/approvals/{approval_id}/approve", paths)
        self.assertIn("/api/work/approvals/{approval_id}/deny", paths)
        self.assertIn("/api/work/confirmations/{confirmation_id}/confirm", paths)
        self.assertIn("/api/work/confirmations/{confirmation_id}/deny", paths)
        self.assertIn("/api/work/confirmations/{confirmation_id}/cancel", paths)

    def test_work_detail_is_not_raw_snapshot(self) -> None:
        self._login()
        brief = TaskBrief(
            objective="Search local knowledge",
            capabilities=("knowledge.search",),
            required_authority="read",
            expected_effect="Retrieved local chunks",
        )
        work_id = self.app.state.services.work.accept(brief, "read")
        detail = build_work_detail(self.app.state.services.work, work_id).as_dict()
        dumped = json.dumps(detail)
        self.assertIn("phase", detail)
        self.assertIn("actions", detail)
        self.assertIn("pending_confirmations", detail)
        self.assertNotIn("success_criteria_json", dumped)
        self.assertNotIn("payload_json", dumped)
        self.assertNotIn("metadata_json", dumped)

    def test_sse_resumes_from_cursor(self) -> None:
        self._login()
        brief = TaskBrief(
            objective="Search local knowledge",
            capabilities=("knowledge.search",),
            required_authority="read",
            expected_effect="Retrieved local chunks",
        )
        work_id = self.app.state.services.work.accept(brief, "read")
        self.app.state.services.work.run(work_id)
        events = self.app.state.services.work.store.list_events(work_id)
        self.assertTrue(events)
        last_id = int(events[-1].id)
        # Cursor contract: only event ids strictly greater than `after` are emitted.
        remaining = [item for item in events if int(item.id) > last_id]
        self.assertEqual(remaining, [])
        bad = self.client.get(
            f"/api/work/{work_id}/events/stream?after=not-an-id"
        )
        self.assertEqual(bad.status_code, 400)
        unauth = TestClient(self.app).get(
            f"/api/work/{work_id}/events/stream?after=0"
        )
        self.assertEqual(unauth.status_code, 401)
        paths = [getattr(route, "path", "") for route in self.app.routes]
        self.assertIn("/api/work/{work_id}/events/stream", paths)

    def test_pdf_index_accept_is_unavailable(self) -> None:
        self._login()
        accepted = self.client.post(
            "/api/work",
            json={
                "brief": {
                    "objective": "Index this PDF manual into knowledge",
                    "capabilities": ["knowledge.ingest_text"],
                    "required_authority": "modify_internal",
                    "expected_effect": "Index local knowledge",
                    "constraints": [],
                },
                "authority_scope": "modify_internal",
            },
            headers=self._headers(),
        )
        self.assertEqual(accepted.status_code, 409)
        body = accepted.json()
        self.assertEqual(body["status"], "unavailable")
        self.assertIn("PDF ingestion", body["reason"])
        listed = self.client.get("/api/work", headers=self._headers())
        self.assertEqual(listed.json()["work"], [])

    def test_work_pause_archive_delete_and_chat_ownership(self) -> None:
        self._login()
        created = self.client.post(
            "/api/work",
            json={
                "brief": {
                    "objective": "Search local knowledge",
                    "capabilities": ["knowledge.search"],
                    "required_authority": "read",
                    "expected_effect": "Retrieved local chunks",
                    "constraints": [],
                },
                "authority_scope": "read",
                "inputs": {"knowledge.search": {"query": "notes", "limit": 1}},
            },
            headers=self._headers(),
        )
        self.assertEqual(created.status_code, 201)
        work_id = created.json()["work_id"]
        paused = self.client.post(
            f"/api/work/{work_id}/pause", json={}, headers=self._headers()
        )
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["phase"], "paused")
        self.assertTrue(is_paused(self.app.state.services.work.store.get_work(work_id)))
        archived = self.client.post(
            f"/api/work/{work_id}/archive",
            json={"archived": True},
            headers=self._headers(),
        )
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json()["phase"], "archived")
        visible = self.client.get("/api/work", headers=self._headers())
        self.assertEqual(visible.json()["work"], [])
        hidden = self.client.get("/api/work?archived=true", headers=self._headers())
        self.assertEqual(len(hidden.json()["work"]), 1)
        deleted = self.client.delete(f"/api/work/{work_id}", headers=self._headers())
        self.assertEqual(deleted.status_code, 200)

        opened = self.client.post(
            "/api/chat/conversations",
            json={"title": "Ops"},
            headers=self._headers(),
        )
        self.assertEqual(opened.status_code, 201)
        cid = opened.json()["id"]
        renamed = self.client.patch(
            f"/api/chat/conversations/{cid}",
            json={"title": "Ops notes", "pinned": True},
            headers=self._headers(),
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["title"], "Ops notes")
        self.assertTrue(renamed.json()["pinned"])
        archived_chat = self.client.patch(
            f"/api/chat/conversations/{cid}",
            json={"archived": True},
            headers=self._headers(),
        )
        self.assertEqual(archived_chat.status_code, 200)
        recents = self.client.get("/api/chat/conversations", headers=self._headers())
        self.assertEqual(recents.json()["conversations"], [])
        stored = self.client.get(
            "/api/chat/conversations?archived=true", headers=self._headers()
        )
        self.assertEqual(stored.json()["conversations"][0]["id"], cid)
        gone = self.client.delete(
            f"/api/chat/conversations/{cid}", headers=self._headers()
        )
        self.assertEqual(gone.status_code, 200)

    def test_spa_fallback_serves_index_and_api_never_falls_through(self) -> None:
        dist = Path(self.tmp.name) / "dist"
        dist.mkdir()
        (dist / "index.html").write_text(
            "<!doctype html><title>Atlas Companion</title><div id='root'></div>",
            encoding="utf-8",
        )
        (dist / "asset.txt").write_text("static-asset", encoding="utf-8")
        app = create_app(
            services=self.app.state.services,
            serve_companion=True,
            companion_dist=dist,
        )
        client = TestClient(app)
        for path in ("/work/new", "/work/work_abc", "/chat/conversation_1"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("Atlas Companion", response.text)
            self.assertIn("text/html", response.headers.get("content-type", ""))
        asset = client.get("/asset.txt")
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.text, "static-asset")
        missing_api = client.get("/api/does-not-exist")
        self.assertEqual(missing_api.status_code, 404)
        self.assertNotIn("Atlas Companion", missing_api.text)

    def test_failed_login_throttle(self) -> None:
        for _ in range(3):
            response = self.client.post(
                "/api/auth/login", json={"password": "wrong-password"}
            )
            self.assertEqual(response.status_code, 401)
        blocked = self.client.post(
            "/api/auth/login", json={"password": "wrong-password"}
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("retry_after_seconds", blocked.json())
        self.assertTrue(int(blocked.headers.get("Retry-After", "0")) >= 1)

    def test_production_cookie_policy_default_is_secure(self) -> None:
        from atlas_api.auth import resolve_cookie_policy
        import os

        previous = {
            key: os.environ.get(key)
            for key in ("ATLAS_SECURE_COOKIES", "ATLAS_ENV")
        }
        try:
            os.environ.pop("ATLAS_SECURE_COOKIES", None)
            os.environ.pop("ATLAS_ENV", None)
            policy = resolve_cookie_policy()
            self.assertTrue(policy.secure)
            self.assertEqual(policy.mode, "production")
            os.environ["ATLAS_ENV"] = "development"
            dev = resolve_cookie_policy()
            self.assertFalse(dev.secure)
            self.assertEqual(dev.mode, "localhost_http_dev")
            os.environ["ATLAS_SECURE_COOKIES"] = "0"
            os.environ["ATLAS_ENV"] = "production"
            explicit = resolve_cookie_policy()
            self.assertFalse(explicit.secure)
            self.assertEqual(explicit.mode, "explicit_insecure_dev")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_waiting_confirmation_not_running_phase(self) -> None:
        self._login()
        brief = {
            "objective": "Send the report",
            "capabilities": ["communication.email.send"],
            "required_authority": "communicate",
            "expected_effect": "external communication",
            "constraints": [],
        }
        accepted = self.client.post(
            "/api/work",
            json={"brief": brief, "authority_scope": "communicate"},
            headers=self._headers(),
        )
        work_id = accepted.json()["work_id"]
        ran = self.client.post(
            f"/api/work/{work_id}/run",
            json={},
            headers=self._headers(),
        )
        detail = ran.json()["detail"]
        self.assertEqual(detail["phase"], "waiting_confirmation")
        self.assertEqual(detail["status"], "waiting")
        self.assertEqual(detail["executions"], [])


if __name__ == "__main__":
    unittest.main()
