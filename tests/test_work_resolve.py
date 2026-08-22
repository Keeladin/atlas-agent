from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import (
    CapabilityBinding,
    CapabilityOutcome,
    ContextPolicy,
    ExecutionBudget,
    RetryPolicy,
)
from atlas_core.tools import ToolDescriptor, ToolGateway, ToolResult
from atlas_core.work import (
    UNAVAILABLE,
    UnavailableWork,
    CapabilityExecutionProfile,
    DeploymentInventory,
    ImplementationResolver,
    WorkError,
    build_work_runtime,
    compile_contract,
)
from tests.work_helpers import run_with_confirmation


RESOLVE_SOURCE = (
    Path(__file__).resolve().parents[1] / "atlas_core" / "work" / "resolve.py"
).read_text(encoding="utf-8")
CONTRACT_SOURCE = (
    Path(__file__).resolve().parents[1] / "atlas_core" / "work" / "contract.py"
).read_text(encoding="utf-8")


def _handler(_request):
    return CapabilityOutcome("pass", output={"ok": True}, receipt={"ok": True})


def _brief(**overrides) -> TaskBrief:
    payload = dict(
        objective="Create automation",
        capabilities=("automation.workflow.create",),
        required_authority="execute_external",
        expected_effect="Create an automation workflow",
    )
    payload.update(overrides)
    return TaskBrief(**payload)


def _profile(version: str = "1.0.0", **overrides) -> CapabilityExecutionProfile:
    payload = dict(
        capability_id="automation.workflow.create",
        version=version,
        implementation=CapabilityBinding(
            "automation.workflow.create", "internal", "record", "1"
        ),
        verifier_id="core.nonempty",
        executor_kind="deterministic",
    )
    payload.update(overrides)
    return CapabilityExecutionProfile(**payload)


class _TrackingInventory(DeploymentInventory):
    def __init__(self) -> None:
        super().__init__()
        self.all_calls = 0

    def all(self):
        self.all_calls += 1
        return super().all()


class DeploymentInventoryTests(unittest.TestCase):
    def test_two_versions_coexist_and_get_without_version_is_latest_non_deprecated(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("1.0.0"), _handler)
        inventory.register(_profile("2.0.0"), _handler)
        self.assertEqual(inventory.get("automation.workflow.create").version, "2.0.0")
        self.assertEqual(
            inventory.get("automation.workflow.create", "1.0.0").version,
            "1.0.0",
        )
        self.assertIsNone(inventory.get("automation.workflow.create", "9.0.0"))

    def test_latest_skips_deprecated(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("1.0.0"), _handler)
        inventory.register(_profile("2.0.0", deprecated=True, replaced_by="3.0.0"), _handler)
        self.assertEqual(inventory.get("automation.workflow.create").version, "1.0.0")
        self.assertEqual(
            inventory.get("automation.workflow.create", "2.0.0").version,
            "2.0.0",
        )

    def test_duplicate_version_is_rejected(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("1.0.0"), _handler)
        with self.assertRaises(ValueError):
            inventory.register(_profile("1.0.0"), _handler)
        with self.assertRaises(TypeError):
            inventory.register(_profile("1.0.0"), _handler, replace=True)

    def test_register_does_not_mint_catalog_identity(self) -> None:
        from atlas_core.capabilities.definition import lookup

        before = lookup("synthetic.engine.only")
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="synthetic.engine.only",
                executor_kind="model",
                verifier_id="core.nonempty",
            )
        )
        self.assertIsNone(lookup("synthetic.engine.only"))
        self.assertIsNone(before)


class ImplementationResolverTests(unittest.TestCase):
    def test_unarmed_pin_is_not_searched_in_inventory(self) -> None:
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=DeploymentInventory(),
        )
        inventory = _TrackingInventory()
        inventory.register(_profile("1.0.0"), _handler)
        report = ImplementationResolver().resolve(contract, inventory)
        self.assertEqual(report.unarmed, ("automation.workflow.create",))
        self.assertEqual(report.mismatches, ())
        self.assertEqual(report.resolved.capabilities, {})
        self.assertEqual(inventory.all_calls, 0)

    def test_exact_version_does_not_substitute(self) -> None:
        inventory = DeploymentInventory()
        def handler_v1(request):
            return CapabilityOutcome("pass", output={"v": 1}, receipt={"ok": True})

        def handler_v2(request):
            return CapabilityOutcome("pass", output={"v": 2}, receipt={"ok": True})

        inventory.register(_profile("1.0.0"), handler_v1)
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=inventory,
        )
        self.assertEqual(
            contract.capability("automation.workflow.create").profile_version,
            "1.0.0",
        )
        inventory.register(_profile("2.0.0"), handler_v2)
        report = ImplementationResolver().resolve(contract, inventory)
        self.assertEqual(report.unarmed, ())
        self.assertEqual(report.mismatches, ())
        bound = report.resolved.capabilities["automation.workflow.create"]
        self.assertEqual(bound.pin.profile_version, "1.0.0")
        self.assertIs(bound.handler, handler_v1)
        self.assertIsNot(bound.handler, handler_v2)

    def test_missing_exact_version_is_mismatch_not_unarmed(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("1.0.0"), _handler)
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=inventory,
        )
        other = DeploymentInventory()
        other.register(_profile("2.0.0"), _handler)
        report = ImplementationResolver().resolve(contract, other)
        self.assertEqual(report.unarmed, ())
        self.assertEqual(
            [item.reason for item in report.mismatches],
            ["version_missing"],
        )
        self.assertEqual(report.resolved.capabilities, {})

    def test_compile_pins_latest_non_deprecated(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("1.0.0"), _handler)
        inventory.register(_profile("2.0.0"), _handler)
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=inventory,
        )
        self.assertEqual(
            contract.capability("automation.workflow.create").profile_version,
            "2.0.0",
        )

    def test_extra_inventory_capability_cannot_enter_resolved_work(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("1.0.0"), _handler)
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="reasoning.general",
                executor_kind="model",
                verifier_id="core.nonempty",
            )
        )
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=inventory,
        )
        report = ImplementationResolver().resolve(contract, inventory)
        self.assertEqual(set(report.resolved.capabilities), {"automation.workflow.create"})
        self.assertNotIn("reasoning.general", report.resolved.capabilities)

    def test_pinned_tool_uses_exact_version(self) -> None:
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="v1", version="1.0.0"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
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
            tools=gateway,
        )
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="v2", version="2.0.0"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        report = ImplementationResolver().resolve(contract, inventory, gateway)
        bound = report.resolved.capabilities["communication.email.send"]
        self.assertEqual(bound.pin.tools, ("mail.deliver@1.0.0",))
        self.assertEqual(bound.tool_specs[0].version, "1.0.0")
        self.assertIn("mail.deliver@1.0.0", bound.tool_handlers)
        self.assertNotIn("mail.deliver@2.0.0", bound.tool_handlers)

    def test_source_does_not_scan_inventory(self) -> None:
        for token in ("inventory.all", ".all(", "manifest("):
            with self.subTest(token=token):
                self.assertNotIn(token, RESOLVE_SOURCE)
                self.assertNotIn(token, CONTRACT_SOURCE)

    def test_same_version_document_changes_mismatch(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("1.0.0"), _handler)
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=inventory,
        )
        divergences = {
            "schemas": {"input_schema": {"type": "object", "required": ["x"]}},
            "verifier": {"verifier_id": "core.receipt"},
            "retry_policy": {
                "retry_policy": RetryPolicy(
                    retry_on=(),
                    stop_on=("pass", "fail", "blocked", "abstain", "rework"),
                )
            },
            "execution_budget": {"budget": ExecutionBudget(max_attempts=1)},
            "privacy": {"privacy": "local_only"},
            "classification": {"data_classification": "sensitive"},
            "eligible_providers": {"eligible_providers": ("local.llama",)},
            "context_profile": {"context_profile": "research"},
            "context_policy": {"context_policy": ContextPolicy(max_tokens=256)},
            "side_effects": {"side_effects": ("external_workflow",)},
            "idempotency": {"idempotent": False},
            "parallel_safe": {"parallel_safe": True},
            "binding": {
                "implementation": CapabilityBinding(
                    "automation.workflow.create", "other", "record", "1"
                )
            },
            "executor_kind": {"executor_kind": "model"},
            "output_kind": {"output_kind": "other_result"},
        }
        for name, overrides in divergences.items():
            other = DeploymentInventory()
            other.register(_profile("1.0.0", **overrides), _handler)
            report = ImplementationResolver().resolve(contract, other)
            with self.subTest(field=name):
                self.assertEqual(report.unarmed, ())
                self.assertEqual(
                    [item.reason for item in report.mismatches],
                    ["profile_mismatch"],
                    name,
                )

    def test_same_version_tool_ref_change_mismatches(self) -> None:
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="v1", version="1.0.0"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
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
            tools=gateway,
        )
        other = DeploymentInventory()
        other.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                implementation=CapabilityBinding(
                    "communication.email.send", "smtp", "send", "1"
                ),
                tools=("mail.deliver@2.0.0",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _handler,
        )
        report = ImplementationResolver().resolve(contract, other, gateway)
        self.assertEqual(
            [item.reason for item in report.mismatches],
            ["profile_mismatch"],
        )

    def test_handler_is_not_swappable_in_process_for_a_version(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("1.0.0"), _handler)

        def other_handler(request):
            return CapabilityOutcome("pass", output={"other": True}, receipt={"ok": True})

        with self.assertRaises(ValueError):
            inventory.register(_profile("1.0.0"), other_handler)

    def test_handler_only_change_is_outside_the_versioned_document(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(_profile("1.0.0"), _handler)
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="execute_external",
            inventory=inventory,
        )

        def other_handler(request):
            return CapabilityOutcome("pass", output={"other": True}, receipt={"ok": True})

        restarted = DeploymentInventory()
        restarted.register(_profile("1.0.0"), other_handler)
        report = ImplementationResolver().resolve(contract, restarted)
        self.assertEqual(report.mismatches, ())
        self.assertIs(
            report.resolved.capabilities["automation.workflow.create"].handler,
            other_handler,
        )


class WorkRuntimeResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas-work.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_post_accept_register_cannot_arm_unarmed_work(self) -> None:
        inventory = _TrackingInventory()
        runtime = build_work_runtime(db_path=self.db, profiles=inventory)
        with self.assertRaises(UnavailableWork):
            runtime.accept(_brief(), "execute_external")
        after_build = inventory.all_calls
        inventory.register(_profile("1.0.0"), _handler)
        self.assertEqual(runtime.store.list_work(), ())
        self.assertEqual(inventory.all_calls, after_build)

    def test_post_accept_newer_version_cannot_replace_the_pin(self) -> None:
        inventory = _TrackingInventory()
        inventory.register(_profile("1.0.0"), _handler)
        runtime = build_work_runtime(db_path=self.db, profiles=inventory)
        work_id = runtime.accept(_brief(), "execute_external")
        after_build = inventory.all_calls
        self.assertEqual(runtime.contract(work_id).capability("automation.workflow.create").profile_version, "1.0.0")
        inventory.register(_profile("2.0.0"), _handler)
        result = run_with_confirmation(runtime, work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            runtime.contract(work_id).capability("automation.workflow.create").profile_version,
            "1.0.0",
        )
        self.assertEqual(runtime._engine.store.list_executions(work_id)[-1].capability_version, "1.0.0")
        self.assertEqual(inventory.all_calls, after_build)

    def test_missing_pin_at_run_fails_closed_not_unavailable(self) -> None:
        first = DeploymentInventory()
        first.register(_profile("1.0.0"), _handler)
        runtime = build_work_runtime(db_path=self.db, profiles=first)
        work_id = runtime.accept(_brief(), "execute_external")
        restarted = DeploymentInventory()
        restarted.register(
            _profile(
                "1.0.0",
                implementation=CapabilityBinding(
                    "automation.workflow.create", "other", "record", "1"
                ),
            ),
            _handler,
        )
        later = build_work_runtime(db_path=self.db, profiles=restarted)
        result = later.run(work_id)
        self.assertNotEqual(result.reason, UNAVAILABLE)
        executions = later._engine.store.list_executions(work_id)
        self.assertTrue(executions)
        self.assertEqual(executions[-1].status, "fail")
        self.assertIn("profile_mismatch", executions[-1].error or "")
        self.assertEqual(
            later.contract(work_id).capability("automation.workflow.create").binding.provider,
            "internal",
        )

    def test_post_accept_tool_cannot_widen_the_contract(self) -> None:
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
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
        runtime = build_work_runtime(
            db_path=self.db, profiles=inventory, tool_gateway=gateway
        )
        work_id = runtime.accept(
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
        )
        gateway.register(
            ToolDescriptor(id="bait.tool", description="Should stay inventory"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        contract = runtime.contract(work_id)
        self.assertEqual(contract.allowed_tools, ("mail.deliver@1.0.0",))
        report = ImplementationResolver().resolve(contract, inventory, gateway)
        bound = report.resolved.capabilities["communication.email.send"]
        self.assertEqual(tuple(spec.id for spec in bound.tool_specs), ("mail.deliver",))


if __name__ == "__main__":
    unittest.main()
