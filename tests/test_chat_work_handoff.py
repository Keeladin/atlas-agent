from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from atlas_api.app import create_app
from atlas_api.auth import AuthService, CookiePolicy
from atlas_api.chat_handoff import (
    MAX_HANDOFF_CONTEXT_CHARS,
    MAX_HANDOFF_CONTEXT_TURNS,
    ChatHandoffError,
    fold_conversation_intent,
)
from atlas_api.compose import DEFAULT_HOST, DEFAULT_PORT, compose_services
from atlas_core.advanced import AdvancedRuntime
from atlas_core.capabilities import CapabilityOutcome
from atlas_core.capabilities.awareness import brief_catalog
from atlas_core.chat import ChatRuntime
from atlas_core.chat.conversations import ConversationStore
from atlas_core.providers import ModelRequest, ModelResponse, ProviderSpec
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    build_work_runtime,
)


class RecordingProvider:
    def __init__(self, payload: dict | None = None) -> None:
        self.spec = ProviderSpec(
            key="fake:local",
            model="fake",
            provider_kind="fake",
            capabilities={"conversation.reply": 1.0, "advanced.brief": 1.0},
            local=True,
            enabled=True,
        )
        self.payload = payload
        self.calls: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if self.payload is not None:
            body = dict(self.payload)
        else:
            body = _payload_for(request.input)
        return ModelResponse(
            json.dumps(body), self.spec.key, self.spec.model, raw={}
        )


def _payload_for(model_input: str) -> dict:
    text = model_input.casefold()
    if "design a chatgpt style agent ui" in text:
        return {
            "objective": "design a chatgpt style agent ui",
            "capabilities": [],
            "reason": "No briefable capability covers product or UI design work.",
            "closest_capability": "coding.software_engineering",
        }
    if "index this pdf manual" in text:
        return {
            "objective": "Index this PDF manual into knowledge",
            "capabilities": ["knowledge.ingest_text"],
            "expected_effect": "Index local knowledge",
            "constraints": ["keep internal"],
        }
    if "do not send this to customers" in text or "weekly report" in text:
        return {
            "objective": "Send the weekly report",
            "capabilities": ["communication.email.send"],
            "expected_effect": "external communication",
            "constraints": ["do not send this to customers"],
        }
    return {
        "objective": "Send the weekly report",
        "capabilities": ["communication.email.send"],
        "expected_effect": "external communication",
        "constraints": [],
    }


def _pass_handler(_request):
    return CapabilityOutcome(
        "pass",
        output={"ok": True},
        receipt={"ok": True},
    )


def _turn(turn_id: str, role: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(id=turn_id, role=role, content=content)


class FoldConversationIntentTests(unittest.TestCase):
    def test_last_user_turn_is_objective_and_earlier_is_notes(self) -> None:
        folded = fold_conversation_intent(
            (
                _turn("t1", "user", "Keep this internal."),
                _turn("t2", "atlas", "Understood."),
                _turn("t3", "user", "Send the weekly report."),
                _turn("t4", "atlas", "That needs Work."),
            ),
            conversation_id="conversation_1",
        )
        self.assertEqual(folded.objective, "Send the weekly report.")
        self.assertIn("User: Keep this internal.", folded.notes or "")
        self.assertIn("Atlas: Understood.", folded.notes or "")
        self.assertNotIn("Send the weekly report.", folded.notes or "")
        self.assertNotIn("That needs Work.", folded.notes or "")

    def test_until_turn_boundary_excludes_later_turns(self) -> None:
        folded = fold_conversation_intent(
            (
                _turn("t1", "user", "First request"),
                _turn("t2", "atlas", "Ack"),
                _turn("t3", "user", "Later request"),
            ),
            conversation_id="conversation_1",
            until_turn_id="t2",
        )
        self.assertEqual(folded.objective, "First request")
        self.assertIsNone(folded.notes)

    def test_submit_as_work_does_not_become_the_objective(self) -> None:
        folded = fold_conversation_intent(
            (
                _turn(
                    "t1",
                    "user",
                    "Send the weekly report. Do not send this to customers.",
                ),
                _turn("t2", "atlas", "This needs Work. Review in Work."),
                _turn("t3", "user", "submit as work"),
            ),
            conversation_id="conversation_1",
            until_turn_id="t3",
        )
        self.assertEqual(
            folded.objective,
            "Send the weekly report. Do not send this to customers.",
        )
        self.assertNotEqual(folded.objective.casefold(), "submit as work")

    def test_atlas_turn_cannot_be_the_objective(self) -> None:
        with self.assertRaises(ChatHandoffError):
            fold_conversation_intent(
                (_turn("t1", "atlas", "I drafted a plan."),),
                conversation_id="conversation_1",
            )

    def test_revision_replaces_objective_and_keeps_notes(self) -> None:
        folded = fold_conversation_intent(
            (
                _turn("t1", "user", "Keep this internal."),
                _turn("t2", "user", "Send the weekly report."),
            ),
            conversation_id="conversation_1",
            revision="Send it only to ops.",
        )
        self.assertEqual(folded.objective, "Send it only to ops.")
        self.assertIn("Keep this internal.", folded.notes or "")

    def test_context_is_bounded_to_recent_turns(self) -> None:
        earlier = [
            _turn(f"u{i}", "user", f"constraint {i}")
            for i in range(MAX_HANDOFF_CONTEXT_TURNS + 4)
        ]
        earlier.append(_turn("obj", "user", "Do the work"))
        folded = fold_conversation_intent(
            earlier, conversation_id="conversation_1"
        )
        notes = folded.notes or ""
        self.assertNotIn("constraint 0", notes)
        self.assertIn(f"constraint {MAX_HANDOFF_CONTEXT_TURNS + 3}", notes)
        self.assertLessEqual(
            notes.count("User:"), MAX_HANDOFF_CONTEXT_TURNS
        )
        self.assertLessEqual(len(notes), MAX_HANDOFF_CONTEXT_CHARS + 16)

    def test_unknown_until_turn_is_an_error(self) -> None:
        with self.assertRaises(ChatHandoffError):
            fold_conversation_intent(
                (_turn("t1", "user", "Hello"),),
                conversation_id="conversation_1",
                until_turn_id="missing",
            )


class ChatWorkHandoffApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.work_db = root / "work.db"
        self.chat_db = root / "chat.db"
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
        work = build_work_runtime(db_path=self.work_db, profiles=inventory)
        self.store = ConversationStore(self.chat_db)
        self.store.initialize()
        self.advanced_provider = RecordingProvider()
        chat = ChatRuntime(
            conversations=self.store,
            provider=RecordingProvider({"text": "unused"}),
            awareness=(),
        )
        advanced = AdvancedRuntime(
            provider=self.advanced_provider,
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
        self.chat = services.chat
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

    def _seed(self, *messages: tuple[str, str]):
        view = self.chat.create_conversation(title="Ops")
        turns = []
        for role, content in messages:
            turns.append(self.store.add_turn(view.id, role=role, content=content))
        return view.id, turns

    def test_conversation_handoff_reads_existing_chat(self) -> None:
        self._login()
        cid, turns = self._seed(
            ("user", "Keep this internal. Do not send this to customers."),
            ("atlas", "Understood."),
            ("user", "Send the weekly report."),
        )
        before = self.store.view(cid)
        planned = self.client.post(
            "/api/advanced/brief",
            json={"conversation_id": cid},
            headers=self._headers(),
        )
        self.assertEqual(planned.status_code, 200, planned.text)
        body = planned.json()
        self.assertEqual(body["status"], "brief")
        self.assertEqual(body["source"]["conversation_id"], cid)
        self.assertIsNone(body["source"]["until_turn_id"])
        self.assertNotIn("authority_scope", body)
        self.assertEqual(body["required_authority"], "communicate")
        advanced_input = self.advanced_provider.calls[-1].input
        self.assertIn("Objective: Send the weekly report.", advanced_input)
        self.assertIn("Do not send this to customers.", advanced_input)
        after = self.store.view(cid)
        self.assertEqual(after.turn_count, before.turn_count)
        self.assertEqual(
            [turn.id for turn in after.turns],
            [turn.id for turn in before.turns],
        )
        self.assertEqual(after.updated_at, before.updated_at)
        self.assertEqual(
            [(turn.role, turn.content) for turn in after.turns],
            [(turn.role, turn.content) for turn in before.turns],
        )
        listed = self.client.get("/api/work", headers=self._headers())
        self.assertEqual(listed.json()["work"], [])
        self.assertEqual(turns[-1].role, "user")

    def test_until_turn_boundary_is_respected(self) -> None:
        self._login()
        cid, turns = self._seed(
            ("user", "Send the weekly report."),
            ("atlas", "That needs Work."),
            ("user", "Ignore this later request."),
        )
        planned = self.client.post(
            "/api/advanced/brief",
            json={"conversation_id": cid, "until_turn_id": turns[0].id},
            headers=self._headers(),
        )
        self.assertEqual(planned.status_code, 200, planned.text)
        self.assertEqual(planned.json()["source"]["until_turn_id"], turns[0].id)
        self.assertIn(
            "Objective: Send the weekly report.",
            self.advanced_provider.calls[-1].input,
        )
        self.assertNotIn("Ignore this later request.", self.advanced_provider.calls[-1].input)

    def test_atlas_turn_cannot_become_objective(self) -> None:
        self._login()
        cid, _turns = self._seed(("atlas", "I drafted a Work request."))
        planned = self.client.post(
            "/api/advanced/brief",
            json={"conversation_id": cid},
            headers=self._headers(),
        )
        self.assertEqual(planned.status_code, 400)
        self.assertIn("no user request", planned.json()["error"])
        self.assertEqual(self.advanced_provider.calls, [])

    def test_unknown_conversation_is_not_found(self) -> None:
        self._login()
        planned = self.client.post(
            "/api/advanced/brief",
            json={"conversation_id": "conversation_missing"},
            headers=self._headers(),
        )
        self.assertEqual(planned.status_code, 404)

    def test_unsupported_handoff_creates_no_work(self) -> None:
        self._login()
        cid, _turns = self._seed(
            (
                "user",
                "design a chatgpt style agent ui for my personal agent called atlas",
            )
        )
        planned = self.client.post(
            "/api/advanced/brief",
            json={"conversation_id": cid},
            headers=self._headers(),
        )
        self.assertEqual(planned.status_code, 200)
        self.assertEqual(planned.json()["status"], "unsupported")
        self.assertEqual(planned.json()["source"]["conversation_id"], cid)
        listed = self.client.get("/api/work", headers=self._headers())
        self.assertEqual(listed.json()["work"], [])

    def test_unavailable_is_only_at_work_accept(self) -> None:
        self._login()
        cid, _turns = self._seed(
            ("user", "Index this PDF manual into knowledge")
        )
        planned = self.client.post(
            "/api/advanced/brief",
            json={"conversation_id": cid},
            headers=self._headers(),
        )
        self.assertEqual(planned.status_code, 200)
        brief = planned.json()
        self.assertEqual(brief["status"], "brief")
        self.assertEqual(brief["capabilities"], ["knowledge.ingest_text"])
        listed = self.client.get("/api/work", headers=self._headers())
        self.assertEqual(listed.json()["work"], [])
        accepted = self.client.post(
            "/api/work",
            json={
                "brief": brief,
                "authority_scope": brief["required_authority"],
            },
            headers=self._headers(),
        )
        self.assertEqual(accepted.status_code, 409)
        self.assertEqual(accepted.json()["status"], "unavailable")
        listed = self.client.get("/api/work", headers=self._headers())
        self.assertEqual(listed.json()["work"], [])

    def test_accept_uses_required_authority_from_brief(self) -> None:
        self._login()
        cid, _turns = self._seed(("user", "Send the weekly report."))
        planned = self.client.post(
            "/api/advanced/brief",
            json={"conversation_id": cid},
            headers=self._headers(),
        )
        brief = planned.json()
        self.assertEqual(brief["required_authority"], "communicate")
        accepted = self.client.post(
            "/api/work",
            json={
                "brief": brief,
                "authority_scope": brief["required_authority"],
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
        self.assertEqual(accepted.json()["authority_scope"], "communicate")

    def test_direct_natural_language_planning_still_works(self) -> None:
        self._login()
        planned = self.client.post(
            "/api/advanced/brief",
            json={"objective": "Send the weekly report"},
            headers=self._headers(),
        )
        self.assertEqual(planned.status_code, 200)
        self.assertEqual(planned.json()["status"], "brief")
        self.assertNotIn("source", planned.json())
        self.assertIn("Objective: Send the weekly report", self.advanced_provider.calls[-1].input)

    def test_revision_keeps_conversation_context(self) -> None:
        self._login()
        cid, _turns = self._seed(
            ("user", "Do not send this to customers."),
            ("user", "Send the weekly report."),
        )
        planned = self.client.post(
            "/api/advanced/brief",
            json={
                "conversation_id": cid,
                "revision": "Send the weekly report to ops only.",
            },
            headers=self._headers(),
        )
        self.assertEqual(planned.status_code, 200)
        advanced_input = self.advanced_provider.calls[-1].input
        self.assertIn("Objective: Send the weekly report to ops only.", advanced_input)
        self.assertIn("Do not send this to customers.", advanced_input)


class CompanionHandoffUiTests(unittest.TestCase):
    def test_chat_uses_conversation_pointer_not_empty_work_new(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "companion"
            / "src"
            / "screens"
            / "Chat.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("Review in Work", source)
        self.assertIn("Send to Work", source)
        self.assertIn("conversation", source)
        self.assertIn("until", source)
        self.assertNotIn('to="/work/new"', source)
        self.assertNotIn("Start work from this", source)

    def test_work_new_review_is_not_execution_steps(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "companion"
            / "src"
            / "screens"
            / "WorkNew.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("Work request", source)
        self.assertIn("Atlas will", source)
        self.assertIn("Permission needed", source)
        self.assertIn("searchParams.get('conversation')", source)
        self.assertIn("authority_scope: brief.required_authority", source)
        self.assertNotIn("AUTHORITIES", source)
        self.assertNotIn("Step 1", source)
        self.assertNotIn("execution step", source)
        self.assertNotIn("execution steps", source)
        self.assertIn("atlas-will", source)
        self.assertNotIn("<ol", source)
        self.assertIn("Tell Atlas what you want done", source)
        self.assertIn("revision", source)
