from __future__ import annotations

import copy
from pathlib import Path
import unittest

from atlas_core.advanced import TaskBrief, TaskCriterion, TaskCriterionBinding
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
from atlas_core.work.contract import work_contract_from_stored


CONTRACT_SOURCE = (
    Path(__file__).resolve().parents[1] / "atlas_core" / "work" / "contract.py"
).read_text(encoding="utf-8")

GOLDEN_COMPILED_AT = "2026-01-01T00:00:00Z"
GOLDEN_WORK_ID = "work_golden_unarmed_knowledge_index"
GOLDEN_SHA256 = "24cf910cf4f2a53e78de81355f0882fac7464a6a8dc2ec4c42916a0b4fef3f49"


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
    def test_model_outcome_and_completion_grounding_policies_are_frozen(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="reasoning.general",
                executor_kind="model",
                model_outcome_policy="claim_bearing",
                verifier_id="core.nonempty",
                eligible_providers=(),
            )
        )
        contract = compile_contract(
            work_id="work_grounded",
            brief=TaskBrief(
                objective="Analyze the supplied maintenance record",
                capabilities=("reasoning.general",),
                required_authority="interpret",
                expected_effect="A bounded analysis",
                completion_grounding_policy="evidence_required",
            ),
            authority_scope="interpret",
            inventory=inventory,
            compiled_at=GOLDEN_COMPILED_AT,
            contract_id="contract_grounded",
        )
        self.assertEqual(contract.completion_grounding_policy, "evidence_required")
        self.assertEqual(contract.criteria[0].satisfaction_policy, "evidence_grounded")
        self.assertEqual(contract.criteria[0].semantic_verification, "required")
        self.assertEqual(contract.capabilities[0].contract_capability_ordinal, 1)
        self.assertEqual(contract.criterion_bindings[0].contract_capability_ordinal, 1)
        self.assertEqual(
            contract.capability("reasoning.general").model_outcome_policy,
            "claim_bearing",
        )
        restored = work_contract_from_stored(
            work_id=contract.work_id,
            contract_id=contract.contract_id,
            sha256=contract.sha256,
            payload=contract.as_payload(),
            compiled_at=contract.compiled_at,
        )
        self.assertEqual(restored.completion_grounding_policy, "evidence_required")
        self.assertEqual(restored.criteria, contract.criteria)
        self.assertEqual(restored.criterion_bindings, contract.criterion_bindings)
        self.assertEqual(
            restored.capability("reasoning.general").model_outcome_policy,
            "claim_bearing",
        )

    def test_completion_grounding_policy_defaults_to_none(self) -> None:
        self.assertEqual(_brief().completion_grounding_policy, "none")

    def test_mixed_criteria_and_occurrence_bindings_are_frozen(self) -> None:
        contract = compile_contract(
            work_id="work_mixed",
            brief=_brief(
                capabilities=("automation.workflow.create", "automation.workflow.create"),
                criteria=(
                    TaskCriterion("Produce the workflow"),
                    TaskCriterion("Ground the workflow rationale", "evidence_grounded", "required"),
                ),
                criterion_bindings=(
                    TaskCriterionBinding(1, 1),
                    TaskCriterionBinding(2, 2),
                ),
            ),
            authority_scope="execute_external",
            inventory=DeploymentInventory(),
        )
        self.assertEqual(tuple(item.satisfaction_policy for item in contract.criteria), ("deliverable", "evidence_grounded"))
        self.assertEqual(
            tuple((item.criterion_ordinal, item.contract_capability_ordinal) for item in contract.criterion_bindings),
            ((1, 1), (2, 2)),
        )

    def test_legacy_capabilities_restore_ordinals_without_rehashing_payload(self) -> None:
        contract = compile_contract(
            work_id="work_legacy_ordinals",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=DeploymentInventory(),
            compiled_at=GOLDEN_COMPILED_AT,
        )
        payload = copy.deepcopy(contract.as_payload())
        payload.pop("criteria")
        payload.pop("criterion_bindings")
        for pin in payload["capabilities"]:
            pin.pop("contract_capability_ordinal")
        _encoded, digest = _payload_hash(payload)
        restored = work_contract_from_stored(
            work_id=contract.work_id,
            contract_id=contract.contract_id,
            sha256=digest,
            payload=payload,
            compiled_at=contract.compiled_at,
        )
        self.assertEqual(restored.capabilities[0].contract_capability_ordinal, 1)
        self.assertEqual(restored.as_payload(), payload)

    def test_legacy_contracts_restore_with_default_policies_without_rehashing(self) -> None:
        contract = compile_contract(
            work_id="work_legacy",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=DeploymentInventory(),
            compiled_at=GOLDEN_COMPILED_AT,
            contract_id="contract_legacy",
        )
        legacy_payload = copy.deepcopy(contract.as_payload())
        legacy_payload.pop("completion_grounding_policy")
        for capability in legacy_payload["capabilities"]:
            capability.pop("model_outcome_policy")
        _encoded, legacy_sha = _payload_hash(legacy_payload)
        restored = work_contract_from_stored(
            work_id=contract.work_id,
            contract_id=contract.contract_id,
            sha256=legacy_sha,
            payload=legacy_payload,
            compiled_at=contract.compiled_at,
        )
        self.assertEqual(restored.completion_grounding_policy, "none")
        self.assertEqual(restored.as_payload(), legacy_payload)

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

    def test_duplicate_brief_ids_become_distinct_contract_occurrences(self) -> None:
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
        self.assertEqual(len(contract.capabilities), 2)
        self.assertEqual(
            tuple(item.contract_capability_ordinal for item in contract.capabilities),
            (1, 2),
        )

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
                capability_id="knowledge.ingest_text",
                tools=("index.write",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                implementation=CapabilityBinding(
                    "knowledge.ingest_text", "internal", "index", "1"
                ),
            ),
            _handler,
        )
        contract = compile_contract(
            work_id="work_1",
            brief=TaskBrief(
                objective="Index then email",
                capabilities=("knowledge.ingest_text", "communication.email.send"),
                required_authority="communicate",
                expected_effect="Index and send",
            ),
            authority_scope="communicate",
            inventory=inventory,
            tools=gateway,
        )
        self.assertEqual(
            tuple(item.capability_id for item in contract.capabilities),
            ("knowledge.ingest_text", "communication.email.send"),
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

    def test_golden_hash_unarmed_knowledge_ingest(self) -> None:
        contract = compile_contract(
            work_id=GOLDEN_WORK_ID,
            brief=TaskBrief(
                objective="Index local knowledge",
                capabilities=("knowledge.ingest_text",),
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
        self.assertFalse(contract.capability("knowledge.ingest_text").armed)
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
