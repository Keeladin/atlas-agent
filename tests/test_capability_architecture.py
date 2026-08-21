from __future__ import annotations

import re
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from tests.capability_fixtures import register_cap

from atlas_core.advanced import TaskBrief
from atlas_core.bootstrap import build_runtime
from atlas_core.capabilities import (
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityOutcome,
    CapabilityRegistry,
    brief_catalog,
    catalog,
    lookup,
    register_intelligence_capabilities,
)
from atlas_core.chat import explain_manifest
from atlas_core.tools import MCPToolBridge, ToolDescriptor, ToolGateway, ToolResult
from atlas_core.work import (
    UNAVAILABLE,
    CapabilityExecutionProfile,
    ExecutionProfileIndex,
    WorkError,
    build_work_runtime,
)


def _email_brief() -> TaskBrief:
    return TaskBrief(
        objective="Send the report",
        capabilities=("communication.email.send",),
        required_authority="communicate",
        expected_effect="external communication",
    )


class CapabilityArchitectureTests(unittest.TestCase):
    def test_capability_exists_without_implementation(self) -> None:
        definition = lookup("communication.email.send")
        self.assertIsNotNone(definition)
        assert definition is not None
        self.assertEqual(definition.id, "communication.email.send")
        self.assertEqual(definition.required_authority, "communicate")
        self.assertEqual(definition.confirmation, "required")
        profiles = ExecutionProfileIndex()
        self.assertIsNone(profiles.get(definition.id))
        self.assertIsNone(lookup("smtp.send"))
        self.assertIsNone(lookup("not.a.capability"))

    def test_provider_replacement_does_not_change_capability_identity(self) -> None:
        definition = lookup("communication.email.send")
        assert definition is not None
        before = (
            definition.id,
            definition.description,
            definition.required_authority,
            definition.confirmation,
            definition.side_effect_class,
        )
        profiles = ExecutionProfileIndex()
        profiles.register(
            CapabilityExecutionProfile(
                capability_id=definition.id,
                implementation=CapabilityBinding(definition.id, "smtp", "send", "1"),
                verifier_id="core.nonempty",
            )
        )
        profiles.register(
            CapabilityExecutionProfile(
                capability_id=definition.id,
                implementation=CapabilityBinding(definition.id, "graph", "sendMail", "1"),
                verifier_id="core.nonempty",
            ),
            replace=True,
        )
        after = lookup("communication.email.send")
        assert after is not None
        self.assertEqual(
            (
                after.id,
                after.description,
                after.required_authority,
                after.confirmation,
                after.side_effect_class,
            ),
            before,
        )
        swapped = profiles.get(definition.id)
        assert swapped is not None
        assert swapped.implementation is not None
        self.assertEqual(swapped.implementation.provider, "graph")
        self.assertEqual(swapped.implementation.implementation, "sendMail")
        self.assertEqual(definition.id, "communication.email.send")

    def test_chat_awareness_exposes_no_execution(self) -> None:
        names = {item.name for item in fields(CapabilityDefinition)}
        self.assertEqual(
            names,
            {
                "id",
                "description",
                "required_authority",
                "confirmation",
                "side_effect_class",
            },
        )
        forbidden = (
            "allowed_tools",
            "tools",
            "bindings",
            "provider",
            "providers",
            "handler",
            "mcp",
            "n8n",
            "verifier_id",
            "retry_policy",
        )
        for item in explain_manifest():
            payload = item.as_dict()
            self.assertEqual(set(payload), names)
            blob = " ".join(payload.values()).casefold()
            for token in forbidden:
                self.assertNotIn(token, payload)
                self.assertNotIn(token, blob)
        catalog_blob = " ".join(
            " ".join(item.as_dict().values()) for item in catalog()
        ).casefold()
        self.assertNotIn("n8n", catalog_blob)
        self.assertNotIn("mcp.", catalog_blob)
        self.assertNotIn("execute_workflow", catalog_blob)

    def test_work_cannot_reach_tool_gateway_without_execution_profile(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "atlas-work.db"
        gateway = _RecordingGateway()
        runtime = build_work_runtime(db_path=db, tool_gateway=gateway)
        work_id = runtime.accept(_email_brief(), "communicate")
        result = runtime.run(work_id)
        self.assertEqual(result.reason, UNAVAILABLE)
        self.assertEqual(result.executions, 0)
        self.assertEqual(gateway.invocations, [])
        self.assertEqual(runtime.get(work_id).status, "planned")

    def test_work_reaches_tool_gateway_only_when_fully_armed(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "atlas-work.db"
        gateway = _RecordingGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver a prepared message"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )

        def handler(request):
            result = gateway.invoke(
                "mail.deliver",
                {"to": "ops@example.invalid"},
                authority_scope="communicate",
            )
            return CapabilityOutcome(
                "pass" if result.ok else "fail",
                output=result.output,
                receipt=result.receipt,
                error=result.error,
            )

        profiles = ExecutionProfileIndex()
        profiles.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                implementation=CapabilityBinding(
                    "communication.email.send", "smtp", "send", "1"
                ),
                tools=("mail.deliver",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            handler,
        )
        runtime = build_work_runtime(db_path=db, tool_gateway=gateway, profiles=profiles)
        work_id = runtime.accept(_email_brief(), "communicate")
        contract = runtime.contract(work_id)
        self.assertEqual(contract.allowed_tools, ("mail.deliver@1.0.0",))
        pin = contract.capability("communication.email.send")
        self.assertIsNotNone(pin.binding)
        self.assertEqual(pin.binding.provider, "smtp")
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(gateway.invocations), 1)
        self.assertEqual(gateway.invocations[0][0], "mail.deliver")

    def test_capability_spec_is_not_part_of_the_architecture(self) -> None:
        import atlas_core.capabilities as capabilities

        self.assertFalse(hasattr(capabilities, "CapabilitySpec"))
        self.assertNotIn("CapabilitySpec", capabilities.__all__)

    def test_handler_and_tool_registration_do_not_create_catalog_identity(self) -> None:
        before = tuple(item.as_dict() for item in catalog())
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="mcp.n8n.execute_workflow", description="Run a workflow"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        registry = CapabilityRegistry()
        register_cap(
            registry,
            "synthetic.engine.only",
            executor_kind="model",
            verifier_id="core.nonempty",
        )
        self.assertEqual(tuple(item.as_dict() for item in catalog()), before)
        self.assertIsNone(lookup("synthetic.engine.only"))
        self.assertIsNone(lookup("mcp.n8n.execute_workflow"))
        self.assertEqual(registry.get("synthetic.engine.only").id, "synthetic.engine.only")

    def test_work_accept_uses_catalog_not_engine_registration(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "atlas-work.db"
        profiles = ExecutionProfileIndex()
        profiles.register(
            CapabilityExecutionProfile(
                capability_id="synthetic.engine.only",
                executor_kind="model",
                verifier_id="core.nonempty",
            ),
            lambda request: CapabilityOutcome("pass", output="executed"),
        )
        runtime = build_work_runtime(db_path=db, profiles=profiles)
        with self.assertRaises(WorkError) as ctx:
            runtime.accept(
                TaskBrief(
                    objective="Do synthetic engine work",
                    capabilities=("synthetic.engine.only",),
                    required_authority="interpret",
                    expected_effect="synthetic",
                ),
                "interpret",
            )
        self.assertIn("Unknown capability", str(ctx.exception))

    def test_catalog_meaning_wins_when_engine_registers_the_same_id(self) -> None:
        meaning = lookup("reasoning.general")
        self.assertIsNotNone(meaning)
        registry = CapabilityRegistry()
        register_intelligence_capabilities(registry)
        self.assertEqual(lookup("reasoning.general"), meaning)
        self.assertEqual(registry.get("reasoning.general").definition, meaning)

    def test_production_modules_do_not_construct_capability_definitions(self) -> None:
        root = Path(__file__).resolve().parents[1] / "atlas_core"
        allowed = {root / "capabilities" / "definition.py"}
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            if path in allowed:
                continue
            source = path.read_text(encoding="utf-8")
            if "CapabilityDefinition(" in source:
                offenders.append(str(path.relative_to(root.parent)))
        self.assertEqual(offenders, [])

    def test_bootstrap_registers_only_catalog_definitions(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        runtime = build_runtime(db_path=Path(tmp.name) / "atlas.db")
        for item in runtime.capabilities.registrations():
            meaning = lookup(item.id)
            self.assertIsNotNone(meaning, item.id)
            self.assertEqual(item.definition, meaning)

    def test_chat_and_advanced_projections_do_not_expand_with_engine_ids(self) -> None:
        self.assertEqual(
            tuple(item.id for item in explain_manifest()),
            ("reasoning.general", "generation.compose", "automation.workflow"),
        )
        self.assertEqual(
            {item.id for item in brief_catalog()},
            {
                "automation.workflow.create",
                "automation.workflow.execute",
                "communication.email.send",
                "knowledge.index",
            },
        )
        self.assertIsNotNone(lookup("planning.general"))
        self.assertIsNotNone(lookup("knowledge.ingest_text"))
        self.assertIsNotNone(lookup("operations.morning_pack.generate"))

    def test_profile_index_and_tool_gateway_cannot_mint_catalog_identity(self) -> None:
        before = {item.id for item in catalog()}
        profiles = ExecutionProfileIndex()
        profiles.register(
            CapabilityExecutionProfile(
                capability_id="synthetic.engine.only",
                executor_kind="model",
                verifier_id="core.nonempty",
            ),
            lambda request: CapabilityOutcome("pass", output="executed"),
        )
        gateway = ToolGateway()
        self.assertFalse(hasattr(gateway, "capability"))
        gateway.register(
            ToolDescriptor(id="mcp.n8n.list_credentials", description="List credentials"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        self.assertEqual({item.id for item in catalog()}, before)
        self.assertIsNone(lookup("synthetic.engine.only"))
        self.assertIsNone(lookup("mcp.n8n.list_credentials"))
        self.assertIsNotNone(profiles.get("synthetic.engine.only"))

    def test_tool_and_mcp_modules_do_not_touch_capability_identity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for rel in (
            "atlas_core/tools.py",
            "atlas_core/mcp_http.py",
            "atlas_core/integrations/n8n_mcp.py",
        ):
            source = (root / rel).read_text(encoding="utf-8")
            for token in ("CapabilityDefinition", "CapabilityRegistry", "catalog()", "require("):
                with self.subTest(file=rel, token=token):
                    self.assertNotIn(token, source)
        self.assertTrue(callable(MCPToolBridge.register_discovered))

    def test_cli_steps_reference_catalog_ids_only(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "atlas_core" / "__main__.py"
        ).read_text(encoding="utf-8")
        ids = re.findall(r'capability="([^"]+)"', source)
        self.assertEqual(
            set(ids),
            {
                "knowledge.ingest_text",
                "knowledge.search",
                "operations.morning_pack.generate",
            },
        )
        for capability_id in ids:
            self.assertIsNotNone(lookup(capability_id), capability_id)

    def test_test_helpers_are_not_production_identity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for path in (root / "atlas_core").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("capability_fixtures", source)
            self.assertNotIn("make_registration", source)
            self.assertNotIn("register_cap(", source)

    def test_architecture_docs_do_not_treat_capability_spec_as_identity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        documents = [
            root / "README.md",
            root / "Atlas Architecture — Runtime and Topology.md",
            *sorted((root / "docs" / "architecture").glob("*.md")),
        ]
        self.assertTrue(documents)
        allowed = ("removed", "has no owner", "do not restore")
        for path in documents:
            text = path.read_text(encoding="utf-8")
            self.assertIn("CapabilityDefinition", text)
            for line_no, line in enumerate(text.splitlines(), 1):
                if "CapabilitySpec" not in line:
                    continue
                lowered = line.casefold()
                self.assertTrue(
                    any(token in lowered for token in allowed),
                    f"{path.name}:{line_no} still treats CapabilitySpec as live: {line}",
                )


class _RecordingGateway(ToolGateway):
    def __init__(self) -> None:
        super().__init__()
        self.invocations: list[tuple[str, dict]] = []

    def invoke(self, tool_id, arguments, *, authority_scope, version=None):
        self.invocations.append((tool_id, arguments))
        return super().invoke(
            tool_id, arguments, authority_scope=authority_scope, version=version
        )
