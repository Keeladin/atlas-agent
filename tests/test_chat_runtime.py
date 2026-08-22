from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import dataclass, fields
from pathlib import Path
from unittest.mock import patch

from atlas_core.chat import CapabilityAwareness, ChatError, ChatRuntime, build_chat_runtime, explain_manifest
from atlas_core.chat.prompts import CHAT_SYSTEM, build_system_prompt
from atlas_core.providers import ModelRequest, ModelResponse, ProviderSpec


def _spec() -> ProviderSpec:
    return ProviderSpec(
        key="chat:test",
        model="test-model",
        provider_kind="fake",
        capabilities={},
        local=True,
        enabled=True,
    )


@dataclass
class FakeProvider:
    spec: ProviderSpec
    requests: list[ModelRequest]
    reply: str

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        text = self.reply
        system = request.system.casefold()
        if "run the maintenance automation" in request.input.casefold():
            if (
                "cannot execute work" in system
                and "work request" in system
                and "automation.workflow" in system
            ):
                text = (
                    "Atlas understands workflow automation as a capability. "
                    "I cannot run it from conversation; execution requires a Work request."
                )
            else:
                text = "Running the maintenance automation now."
        return ModelResponse(text, self.spec.key, self.spec.model, {}, {})


class ChatRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.chat_db = self.root / "atlas-chat.db"
        self.work_db = self.root / "atlas.db"
        self.provider = FakeProvider(_spec(), [], "A torque converter is a fluid coupling.")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _runtime(self, provider: FakeProvider | None = None) -> ChatRuntime:
        return build_chat_runtime(db_path=self.chat_db, provider=provider or self.provider)

    def test_awareness_is_explain_projection_only(self) -> None:
        names = {item.name for item in fields(CapabilityAwareness)}
        self.assertEqual(
            names,
            {"id", "description", "required_authority", "confirmation", "side_effect_class"},
        )
        manifest = explain_manifest()
        self.assertTrue(any(item.id == "automation.workflow" for item in manifest))
        joined = " ".join(item.description for item in manifest).casefold()
        self.assertIn("workflow automation", joined)
        self.assertNotIn("execute_workflow", joined)

    def test_torque_converter_is_conversation_not_work(self) -> None:
        opened: list[str] = []
        real_connect = sqlite3.connect

        def tracking_connect(database, *args, **kwargs):
            opened.append(str(database))
            return real_connect(database, *args, **kwargs)

        with patch("atlas_core.chat.conversations.sqlite3.connect", tracking_connect):
            chat = self._runtime()
            result = chat.respond("What is a torque converter?")

        self.assertEqual(result.reply, "A torque converter is a fluid coupling.")
        self.assertTrue(result.conversation_id)
        self.assertEqual(result.conversation.turn_count, 2)
        self.assertEqual(result.conversation.turns[0].role, "user")
        self.assertEqual(result.conversation.turns[0].content, "What is a torque converter?")
        self.assertEqual(result.conversation.turns[1].role, "atlas")
        self.assertEqual(result.conversation.turns[1].content, result.reply)
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.provider.requests[0].capability_id, "conversation.reply")
        self.assertIn("You are Atlas in conversation.", self.provider.requests[0].system)
        self.assertIn("You cannot execute work.", self.provider.requests[0].system)

        listed = chat.list_conversations()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].id, result.conversation_id)
        loaded = chat.conversation(result.conversation_id)
        self.assertEqual(loaded.turn_count, 2)

        self.assertTrue(self.chat_db.is_file())
        self.assertFalse(self.work_db.exists())
        self.assertTrue(all(self.chat_db.name in path for path in opened))
        self.assertFalse(any("atlas.db" == Path(path).name for path in opened))

    def test_work_looking_request_does_not_execute(self) -> None:
        chat = self._runtime()
        result = chat.respond("Run the maintenance automation")
        reply = result.reply.casefold()
        self.assertIn("workflow automation", reply)
        self.assertIn("work request", reply)
        self.assertNotIn("running the maintenance automation now", reply)
        request = self.provider.requests[0]
        self.assertIn("automation.workflow", request.system)
        self.assertIn(CHAT_SYSTEM.splitlines()[0], request.system)
        self.assertIn("cannot call tools", request.system)
        self.assertFalse(self.work_db.exists())
        self.assertEqual(_table_names(self.chat_db), {"conversations", "conversation_turns"})

    def test_chat_database_has_no_work_tables(self) -> None:
        chat = self._runtime()
        chat.respond("What is a torque converter?")
        tables = _table_names(self.chat_db)
        self.assertEqual(tables, {"conversations", "conversation_turns"})
        columns = _columns(self.chat_db, "conversation_turns")
        self.assertEqual(
            columns,
            {
                "id",
                "conversation_id",
                "role",
                "content",
                "metadata_json",
                "created_at",
            },
        )
        self.assertNotIn("tasks", tables)
        self.assertNotIn("executions", tables)
        self.assertNotIn("artifacts", tables)
        self.assertFalse(self.work_db.exists())

    def test_build_chat_runtime_is_the_constructor(self) -> None:
        chat = build_chat_runtime(db_path=self.chat_db, provider=self.provider)
        self.assertIsInstance(chat, ChatRuntime)
        with self.assertRaises(ChatError):
            build_chat_runtime(db_path=self.chat_db)

    def test_system_prompt_forbids_execution(self) -> None:
        prompt = build_system_prompt(explain_manifest())
        self.assertIn("You are Atlas in conversation.", prompt)
        self.assertIn("You cannot execute work.", prompt)
        self.assertIn("You cannot create tasks.", prompt)
        self.assertIn("You cannot call tools.", prompt)
        self.assertIn("Work request", prompt)
        self.assertNotIn("execute_workflow", prompt)
        self.assertIn("Atlas identity (runtime truth", prompt)
        self.assertIn("user's own Atlas host", prompt)
        self.assertIn("Turn provider facts", prompt)

    def test_who_are_you_uses_atlas_identity_not_provider(self) -> None:
        chat = self._runtime()
        result = chat.respond("who are you?")
        reply = result.reply.casefold()
        self.assertIn("atlas", reply)
        self.assertIn("host", reply)
        self.assertNotIn("i'm a cloud", reply)
        self.assertNotIn("test-model", reply.casefold())
        self.assertEqual(self.provider.requests, [])

    def test_what_model_keeps_identity_separate_from_provider(self) -> None:
        chat = self._runtime()
        result = chat.respond("what model are you?")
        reply = result.reply.casefold()
        self.assertIn("atlas", reply)
        self.assertIn("not a foundation-model brand", reply)
        self.assertIn("test-model", reply)
        self.assertIn("local model provider", reply)
        self.assertEqual(self.provider.requests, [])

    def test_are_you_cloud_based_is_false_for_atlas_product(self) -> None:
        chat = self._runtime()
        result = chat.respond("are you a cloud based model?")
        reply = result.reply.casefold()
        self.assertTrue(reply.startswith("no."))
        self.assertIn("own atlas host", reply)
        self.assertIn("does not make atlas cloud-based", reply)
        self.assertIn("local model provider", reply)
        self.assertNotIn("yes, i'm a cloud", reply)
        self.assertEqual(self.provider.requests, [])

    def test_local_provider_turn_reports_local_placement(self) -> None:
        provider = FakeProvider(_spec(), [], "should not be used")
        chat = self._runtime(provider)
        result = chat.respond("Are you running locally?")
        self.assertIn("local model provider", result.reply.casefold())
        self.assertIn("own atlas host", result.reply.casefold())
        self.assertEqual(provider.requests, [])
        self.assertTrue(chat.turn_provider.local)

    def test_cloud_provider_turn_does_not_make_atlas_cloud_based(self) -> None:
        cloud = FakeProvider(
            ProviderSpec(
                key="cloud:xai",
                model="grok-test",
                provider_kind="openai_compatible_chat",
                capabilities={},
                local=False,
                enabled=True,
            ),
            [],
            "Yes, I'm a cloud-based AI",
        )
        chat = self._runtime(cloud)
        result = chat.respond("are you cloud based?")
        reply = result.reply.casefold()
        self.assertTrue(reply.startswith("no."))
        self.assertIn("cloud model provider", reply)
        self.assertIn("does not make atlas cloud-based", reply)
        self.assertNotIn("yes, i'm a cloud-based ai", reply)
        self.assertEqual(cloud.requests, [])
        self.assertFalse(chat.turn_provider.local)

        model_reply = chat.respond("what model are you?").reply.casefold()
        self.assertIn("grok-test", model_reply)
        self.assertIn("cloud model provider", model_reply)
        self.assertIn("atlas", model_reply)
        self.assertEqual(cloud.requests, [])

    def test_rename_pin_archive_delete(self) -> None:
        chat = self._runtime()
        first = chat.respond("Hello Atlas")
        second = chat.create_conversation(title="Later")
        chat.rename_conversation(first.conversation_id, "Torque notes")
        renamed = chat.conversation(first.conversation_id)
        self.assertEqual(renamed.title, "Torque notes")
        self.assertEqual(renamed.turn_count, 2)

        chat.pin_conversation(second.id, True)
        recents = chat.list_conversations()
        self.assertEqual(recents[0].id, second.id)
        self.assertTrue(recents[0].pinned)

        chat.archive_conversation(first.conversation_id, True)
        recents = chat.list_conversations()
        self.assertEqual([item.id for item in recents], [second.id])
        archived = chat.list_conversations(archived=True)
        self.assertEqual([item.id for item in archived], [first.conversation_id])
        self.assertTrue(archived[0].archived_at)

        chat.delete_conversation(first.conversation_id)
        self.assertEqual(chat.list_conversations(archived=True), ())
        remaining = chat.list_conversations()
        self.assertEqual([item.id for item in remaining], [second.id])


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as db:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}
