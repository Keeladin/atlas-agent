from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, fields
from pathlib import Path
from unittest.mock import patch

from atlas_core.advanced import (
    AdvancedError,
    AdvancedRuntime,
    TaskBrief,
    UnsupportedBrief,
    build_advanced_runtime,
)
from atlas_core.advanced.prompts import ADVANCED_SYSTEM
from atlas_core.capabilities.awareness import brief_catalog
from atlas_core.providers import ModelRequest, ModelResponse, ProviderSpec


def _spec() -> ProviderSpec:
    return ProviderSpec(
        key="advanced:test",
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
    payload: dict | None = None

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.payload is not None:
            body = self.payload
        else:
            body = _payload_for(request.input)
        return ModelResponse(json.dumps(body), self.spec.key, self.spec.model, {}, {})


def _payload_for(model_input: str) -> dict:
    text = model_input.casefold()
    if "send this maintenance report to management" in text:
        return {
            "objective": "Send this maintenance report to management",
            "capabilities": ["communication.email.send"],
            "expected_effect": "external communication",
        }
    if "create automation that processes daily reports" in text:
        return {
            "objective": "Create automation that processes daily reports",
            "capabilities": ["automation.workflow.create"],
            "expected_effect": "Create an automation workflow",
        }
    if "create a maintenance reporting automation" in text:
        return {
            "objective": "Create a maintenance reporting automation",
            "capabilities": ["automation.workflow.create"],
            "expected_effect": "Create an automation workflow",
        }
    return {
        "objective": model_input,
        "capabilities": ["knowledge.index"],
        "expected_effect": "Index local knowledge",
    }


class AdvancedRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.provider = FakeProvider(_spec(), [])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _runtime(self, provider: FakeProvider | None = None) -> AdvancedRuntime:
        return build_advanced_runtime(provider=provider or self.provider)

    def test_task_brief_is_a_value_object(self) -> None:
        names = {item.name for item in fields(TaskBrief)}
        self.assertEqual(
            names,
            {
                "objective",
                "capabilities",
                "required_authority",
                "expected_effect",
                "constraints",
                "deliverable_kind",
                "notes",
            },
        )
        ids = {item.id for item in brief_catalog()}
        self.assertEqual(
            ids,
            {
                "automation.workflow.create",
                "automation.workflow.execute",
                "communication.email.send",
                "knowledge.index",
            },
        )

    def test_maintenance_automation_brief_does_not_create_work(self) -> None:
        with patch("sqlite3.connect", side_effect=AssertionError("Advanced must not open sqlite")):
            runtime = self._runtime()
            brief = runtime.brief("Create a maintenance reporting automation")

        self.assertIsInstance(brief, TaskBrief)
        self.assertEqual(brief.objective, "Create a maintenance reporting automation")
        self.assertEqual(brief.capabilities, ("automation.workflow.create",))
        self.assertEqual(brief.required_authority, "execute_external")
        self.assertEqual(brief.expected_effect, "Create an automation workflow")
        self.assertFalse(hasattr(brief, "authority_scope"))
        self.assertNotIn("task_id", brief.as_dict())
        self.assertEqual(len(self.provider.requests), 1)
        system = self.provider.requests[0].system
        self.assertIn(ADVANCED_SYSTEM.splitlines()[0], system)
        self.assertIn("automation.workflow.create", system)
        self.assertIn("cannot grant authority", system)
        self.assertIn("Never include authority_scope", system)
        self.assertFalse((self.root / "atlas-work.db").exists())
        self.assertFalse((self.root / "atlas-chat.db").exists())
        self.assertFalse((self.root / "atlas.db").exists())

    def test_communication_brief_does_not_send_or_confirm(self) -> None:
        runtime = self._runtime()
        brief = runtime.brief("Send this maintenance report to management")
        self.assertEqual(brief.capabilities, ("communication.email.send",))
        self.assertEqual(brief.required_authority, "communicate")
        self.assertEqual(brief.expected_effect, "external communication")
        self.assertEqual(brief.deliverable_kind, "communication")
        self.assertNotIn("authority_scope", brief.as_dict())
        request = self.provider.requests[0]
        self.assertIn("communication.email.send", request.system)
        self.assertNotIn("send_email", request.system)
        self.assertNotIn("n8n", request.system)

    def test_implementation_details_are_not_selected(self) -> None:
        runtime = self._runtime()
        brief = runtime.brief("Create automation that processes daily reports")
        self.assertEqual(brief.capabilities, ("automation.workflow.create",))
        self.assertNotIn("n8n.create_workflow_from_code", brief.capabilities)
        self.assertNotIn("execute_workflow", brief.capabilities)
        dumped = json.dumps(brief.as_dict())
        self.assertNotIn("n8n", dumped)
        self.assertNotIn("create_workflow_from_code", dumped)

    def test_tool_style_capability_is_rejected(self) -> None:
        provider = FakeProvider(
            _spec(),
            [],
            {
                "objective": "Create automation",
                "capabilities": ["n8n.create_workflow_from_code"],
            },
        )
        runtime = self._runtime(provider)
        with self.assertRaises(AdvancedError):
            runtime.brief("Create automation that processes daily reports")

    def test_zero_capability_output_is_unsupported_not_task_brief(self) -> None:
        objective = (
            "design a chatgpt style agent ui for my personal agent called atlas"
        )
        provider = FakeProvider(
            _spec(),
            [],
            {
                "objective": objective,
                "capabilities": [],
                "reason": (
                    "No briefable capability covers product or UI design work."
                ),
                "closest_capability": "coding.software_engineering",
            },
        )
        runtime = self._runtime(provider)
        result = runtime.brief(objective)
        self.assertIsInstance(result, UnsupportedBrief)
        self.assertNotIsInstance(result, TaskBrief)
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.objective, objective)
        self.assertIn("UI design", result.reason)
        self.assertEqual(result.closest_capability, "coding.software_engineering")
        payload = result.as_dict()
        self.assertEqual(payload["status"], "unsupported")
        self.assertNotIn("capabilities", payload)
        self.assertNotIn("required_authority", payload)
        with self.assertRaises(ValueError):
            TaskBrief(
                objective=objective,
                capabilities=(),
                required_authority="interpret",
                expected_effect="design",
            )

    def test_non_briefable_known_capability_is_unsupported(self) -> None:
        provider = FakeProvider(
            _spec(),
            [],
            {
                "objective": "Implement a small UI change",
                "capabilities": ["coding.software_engineering"],
                "expected_effect": "code change",
            },
        )
        runtime = self._runtime(provider)
        result = runtime.brief("Implement a small UI change")
        self.assertIsInstance(result, UnsupportedBrief)
        self.assertEqual(result.closest_capability, "coding.software_engineering")
        self.assertIn("coding.software_engineering", result.reason)

    def test_creates_no_database(self) -> None:
        runtime = self._runtime()
        runtime.brief("Create a maintenance reporting automation")
        sqlite_files = list(self.root.glob("*.db"))
        self.assertEqual(sqlite_files, [])

    def test_build_advanced_runtime_is_the_constructor(self) -> None:
        runtime = build_advanced_runtime(provider=self.provider)
        self.assertIsInstance(runtime, AdvancedRuntime)
        with self.assertRaises(AdvancedError):
            build_advanced_runtime()

    def test_required_authority_is_requested_not_granted(self) -> None:
        runtime = self._runtime()
        brief = runtime.brief("Send this maintenance report to management")
        payload = self.provider.requests[0]
        self.assertIn("Never include authority_scope", payload.system)
        self.assertEqual(brief.required_authority, "communicate")
        with self.assertRaises(TypeError):
            TaskBrief(  # type: ignore[call-arg]
                objective="x",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
                authority_scope="communicate",
            )
