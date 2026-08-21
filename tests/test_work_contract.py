from __future__ import annotations

from pathlib import Path
import unittest

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import CapabilityBinding, CapabilityOutcome
from atlas_core.runtime_types import RuntimeBudget
from atlas_core.work.store_common import _payload_hash
from atlas_core.tools import ToolDescriptor, ToolGateway, ToolResult
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    WorkError,
    compile_contract,
)


CONTRACT_SOURCE = (
    Path(__file__).resolve().parents[1] / "atlas_core" / "work" / "contract.py"
).read_text(encoding="utf-8")

GOLDEN_COMPILED_AT = "2026-01-01T00:00:00Z"
GOLDEN_WORK_ID = "work_golden_unarmed_knowledge_index"
GOLDEN_SHA256 = "45cc6ac3d37940d8d4639e9455d1d2a451d78740f566faf71d53b1fe71a901a8"


def _brief(**overrides) -> TaskBrief:
    payload = dict(
        objective="Create automation",
        capabilities=("automation.workflow.create",),
        required_authority="execute_external",
        expected_effect="Create an automation workflow",
    )
    payload.update(overrides)
    return TaskBrief(**payload)


def _handler(_request):
    return CapabilityOutcome("pass", output={"ok": True})


class _TrackingIndex(DeploymentInventory):
    def __init__(self) -> None:
        super().__init__()
        self.all_calls = 0

    def all(self) -> tuple[CapabilityExecutionProfile, ...]:
        self.all_calls += 1
        return super().all()


class _TrackingGateway(ToolGateway):
    def __init__(self) -> None:
        super().__init__()
        self.manifest_calls = 0
        self.gets: list[tuple[str, str | None]] = []

    def manifest(self, *, include_all_versions: bool = False):
        self.manifest_calls += 1
        return super().manifest(include_all_versions=include_all_versions)

    def get(self, tool_id: str, version: str | None = None):
        self.gets.append((tool_id, version))
        return super().get(tool_id, version)


class WorkContractCompileTests(unittest.TestCase):
    def test_unarmed_catalog_capability_stays_in_contract(self) -> None:
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=DeploymentInventory(),
        )
        self.assertEqual(
            tuple(item.capability_id for item in contract.capabilities),
            ("automation.workflow.create",),
        )
        pin = contract.capability("automation.workflow.create")
        self.assertFalse(pin.armed)
        self.assertIsNone(pin.profile_version)
        self.assertEqual(pin.tools, ())
        self.assertIsNone(pin.binding)
        self.assertEqual(contract.allowed_tools, ())
        self.assertEqual(
            contract.confirmation_requirements,
            ("automation.workflow.create",),
        )
        self.assertEqual(contract.authority_scope, "execute_external")
        self.assertEqual(contract.success_criteria, ("Create an automation workflow",))
        self.assertTrue(contract.contract_id.startswith("contract_"))
        self.assertEqual(len(contract.sha256), 64)
        self.assertNotIn("contract_id", contract.as_payload())
        self.assertNotIn("sha256", contract.as_payload())
        _encoded, digest = _payload_hash(contract.as_payload())
        self.assertEqual(contract.sha256, digest)

    def test_unknown_capability_is_work_error(self) -> None:
        brief = TaskBrief(
            objective="Synthetic",
            capabilities=("synthetic.engine.only",),
            required_authority="interpret",
            expected_effect="synthetic",
        )
        with self.assertRaises(WorkError) as ctx:
            compile_contract(
                work_id="work_1",
                brief=brief,
                authority_scope="interpret",
                inventory=DeploymentInventory(),
            )
        self.assertIn("Unknown capability", str(ctx.exception))

    def test_insufficient_authority_is_work_error(self) -> None:
        with self.assertRaises(WorkError) as ctx:
            compile_contract(
                work_id="work_1",
                brief=_brief(),
                authority_scope="read",
                inventory=DeploymentInventory(),
            )
        self.assertIn("required_authority", str(ctx.exception))

    def test_unrelated_profile_does_not_enter_the_contract(self) -> None:
        inventory = _TrackingIndex()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="reasoning.general",
                executor_kind="model",
                verifier_id="core.nonempty",
            )
        )
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                implementation=CapabilityBinding(
                    "automation.workflow.create", "internal", "record", "1"
                ),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _handler,
        )
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=inventory,
        )
        self.assertEqual(
            tuple(item.capability_id for item in contract.capabilities),
            ("automation.workflow.create",),
        )
        self.assertEqual(inventory.all_calls, 0)
        with self.assertRaises(WorkError):
            contract.capability("reasoning.general")

    def test_extra_gateway_tool_is_not_named_on_the_contract(self) -> None:
        gateway = _TrackingGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver mail"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        gateway.register(
            ToolDescriptor(id="bait.tool", description="Should stay inventory"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        inventory = _TrackingIndex()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                implementation=CapabilityBinding(
                    "communication.email.send", "smtp", "send", "1"
                ),
                tools=("mail.deliver",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _handler,
        )
        contract = compile_contract(
            work_id="work_1",
            brief=TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            authority_scope="communicate",
            inventory=inventory,
            tools=gateway,
        )
        self.assertEqual(contract.allowed_tools, ("mail.deliver@1.0.0",))
        self.assertEqual(contract.capability("communication.email.send").tools, ("mail.deliver@1.0.0",))
        self.assertEqual(gateway.manifest_calls, 0)
        self.assertEqual(gateway.gets, [("mail.deliver", None)])
        self.assertTrue(contract.capability("communication.email.send").armed)

    def test_missing_named_tool_is_unarmed_not_work_error(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                implementation=CapabilityBinding(
                    "communication.email.send", "smtp", "send", "1"
                ),
                tools=("mail.deliver",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _handler,
        )
        contract = compile_contract(
            work_id="work_1",
            brief=TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            authority_scope="communicate",
            inventory=inventory,
            tools=ToolGateway(),
        )
        pin = contract.capability("communication.email.send")
        self.assertFalse(pin.armed)
        self.assertEqual(pin.tools, ())
        self.assertIsNone(pin.profile_version)
        self.assertEqual(contract.allowed_tools, ())

    def test_morning_shaped_profile_arms_without_binding(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="operations.morning_pack.generate",
                executor_kind="deterministic",
                verifier_id="core.nonempty",
            ),
            _handler,
        )
        contract = compile_contract(
            work_id="work_1",
            brief=TaskBrief(
                objective="Generate the morning pack",
                capabilities=("operations.morning_pack.generate",),
                required_authority="read",
                expected_effect="Generate the frozen V1 TMM morning pack from a configured source.",
            ),
            authority_scope="read",
            inventory=inventory,
        )
        pin = contract.capability("operations.morning_pack.generate")
        self.assertTrue(pin.armed)
        self.assertIsNone(pin.binding)
        self.assertEqual(pin.executor_kind, "deterministic")
        self.assertEqual(pin.profile_version, "1.0.0")
        self.assertEqual(pin.tools, ())

    def test_human_without_handler_is_armed(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                executor_kind="human",
                verification_required=False,
            )
        )
        contract = compile_contract(
            work_id="work_1",
            brief=TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            authority_scope="communicate",
            inventory=inventory,
        )
        pin = contract.capability("communication.email.send")
        self.assertTrue(pin.armed)
        self.assertEqual(pin.executor_kind, "human")
        self.assertIsNone(pin.binding)

    def test_model_without_handler_is_armed(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="reasoning.general",
                executor_kind="model",
                verifier_id="core.nonempty",
            )
        )
        contract = compile_contract(
            work_id="work_1",
            brief=TaskBrief(
                objective="Explain the idea",
                capabilities=("reasoning.general",),
                required_authority="interpret",
                expected_effect="Explain ideas and answer questions from stated information.",
            ),
            authority_scope="interpret",
            inventory=inventory,
        )
        self.assertTrue(contract.capability("reasoning.general").armed)

    def test_deterministic_without_handler_is_unarmed_even_with_binding(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                implementation=CapabilityBinding(
                    "automation.workflow.create", "internal", "record", "1"
                ),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            )
        )
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=inventory,
        )
        pin = contract.capability("automation.workflow.create")
        self.assertFalse(pin.armed)
        self.assertIsNone(pin.binding)

    def test_binding_snapshots_profile_implementation_only(self) -> None:
        inventory = DeploymentInventory()
        binding = CapabilityBinding(
            "automation.workflow.create", "internal", "record", "1"
        )
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                implementation=binding,
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _handler,
        )
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=inventory,
        )
        self.assertEqual(contract.capability("automation.workflow.create").binding, binding)

    def test_duplicate_brief_ids_are_an_exact_set(self) -> None:
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(
                capabilities=(
                    "automation.workflow.create",
                    "automation.workflow.create",
                )
            ),
            authority_scope="execute_external",
            inventory=DeploymentInventory(),
        )
        self.assertEqual(len(contract.capabilities), 1)

    def test_multi_capability_brief_preserves_order_and_unions_armed_tools(self) -> None:
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver mail"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        gateway.register(
            ToolDescriptor(id="index.write", description="Write index"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                tools=("mail.deliver",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                implementation=CapabilityBinding(
                    "communication.email.send", "smtp", "send", "1"
                ),
            ),
            _handler,
        )
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.index",
                tools=("index.write",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                implementation=CapabilityBinding(
                    "knowledge.index", "internal", "index", "1"
                ),
            ),
            _handler,
        )
        contract = compile_contract(
            work_id="work_1",
            brief=TaskBrief(
                objective="Index then email",
                capabilities=("knowledge.index", "communication.email.send"),
                required_authority="communicate",
                expected_effect="Index and send",
            ),
            authority_scope="communicate",
            inventory=inventory,
            tools=gateway,
        )
        self.assertEqual(
            tuple(item.capability_id for item in contract.capabilities),
            ("knowledge.index", "communication.email.send"),
        )
        self.assertEqual(
            contract.allowed_tools,
            ("index.write@1.0.0", "mail.deliver@1.0.0"),
        )
        self.assertEqual(contract.confirmation_requirements, ("communication.email.send",))

    def test_work_budget_is_pinned_on_the_payload(self) -> None:
        budget = RuntimeBudget(max_executions=7, max_cycles=3, max_model_calls=1)
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=DeploymentInventory(),
            work_budget=budget,
        )
        self.assertEqual(contract.work_budget, budget)
        self.assertEqual(contract.as_payload()["work_budget"]["max_executions"], 7)

    def test_versioned_tool_ref_is_pinned_exactly(self) -> None:
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="v1", version="1.0.0"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="v2", version="2.0.0"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                tools=("mail.deliver@1.0.0",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                implementation=CapabilityBinding(
                    "communication.email.send", "smtp", "send", "1"
                ),
            ),
            _handler,
        )
        contract = compile_contract(
            work_id="work_1",
            brief=TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            authority_scope="communicate",
            inventory=inventory,
            tools=gateway,
        )
        self.assertEqual(
            contract.capability("communication.email.send").tools,
            ("mail.deliver@1.0.0",),
        )

    def test_golden_hash_unarmed_knowledge_index(self) -> None:
        contract = compile_contract(
            work_id=GOLDEN_WORK_ID,
            brief=TaskBrief(
                objective="Index local knowledge",
                capabilities=("knowledge.index",),
                required_authority="modify_internal",
                expected_effect="Index local knowledge",
            ),
            authority_scope="modify_internal",
            inventory=DeploymentInventory(),
            work_budget=RuntimeBudget(),
            compiled_at=GOLDEN_COMPILED_AT,
            contract_id="contract_golden",
        )
        self.assertEqual(contract.contract_id, "contract_golden")
        self.assertFalse(contract.capability("knowledge.index").armed)
        _encoded, digest = _payload_hash(contract.as_payload())
        self.assertEqual(digest, GOLDEN_SHA256)
        self.assertEqual(contract.sha256, GOLDEN_SHA256)

    def test_compile_module_does_not_scan_inventory_or_legacy_engines(self) -> None:
        forbidden = (
            "inventory.all",
            ".all(",
            "manifest(",
            "CapabilityBindingIndex",
            "CapabilityRegistry",
            "TaskPlanner",
            "MCPToolBridge",
            "TaskRuntime",
            "RuntimeFrame",
            "assemble_frame",
            "atlas_core.planner",
            "atlas_core.integrations",
            "atlas_core.mcp_http",
            "atlas_core.chat",
            "atlas_companion",
            "n8n",
            "providers()",
            ".all(",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, CONTRACT_SOURCE)


if __name__ == "__main__":
    unittest.main()
