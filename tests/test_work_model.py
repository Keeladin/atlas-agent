from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import ExecutionBudget
from atlas_core.providers import ModelResponse, ModelRouter, ProviderRegistry, ProviderSpec
from atlas_core.runtime_types import RuntimeBudget
from atlas_core.tools import ToolDescriptor, ToolGateway, ToolResult
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    build_work_runtime,
)


class FakeProvider:
    def __init__(
        self,
        spec: ProviderSpec,
        *,
        text: str = "A bounded explanation of the request.",
        error: Exception | None = None,
    ) -> None:
        self.spec = spec
        self.text = text
        self.error = error
        self.calls: list = []

    def generate(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return ModelResponse(
            self.text,
            self.spec.key,
            self.spec.model,
            raw={},
            metrics={"input_tokens": 10, "output_tokens": 8},
        )


def _spec(key: str, *, local: bool, competence: float, **overrides) -> ProviderSpec:
    payload = dict(
        key=key,
        model=f"model-{key}",
        provider_kind="fake",
        capabilities={"reasoning.general": competence},
        local=local,
        enabled=True,
        max_context_chars=128_000,
        priority=50,
        latency_rank=50,
    )
    payload.update(overrides)
    return ProviderSpec(**payload)


def _profile(**overrides) -> CapabilityExecutionProfile:
    payload = dict(
        capability_id="reasoning.general",
        executor_kind="model",
        verifier_id="core.nonempty",
        eligible_providers=("local:primary",),
        privacy="cloud_allowed",
    )
    payload.update(overrides)
    return CapabilityExecutionProfile(**payload)


def _brief() -> TaskBrief:
    return TaskBrief(
        objective="Explain the request",
        capabilities=("reasoning.general",),
        required_authority="interpret",
        expected_effect="A bounded explanation",
    )


class WorkModelExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas-work.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _router(self, *providers: FakeProvider) -> ModelRouter:
        registry = ProviderRegistry()
        for provider in providers:
            registry.register(provider)
        return ModelRouter(registry)

    def _run(self, inventory, router, *, gateway=None, budget=None):
        runtime = build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            tool_gateway=gateway,
            model_router=router,
            budget=budget,
        )
        work_id = runtime.accept(_brief(), "interpret")
        result = runtime.run(work_id)
        return runtime, work_id, result

    def test_eligible_provider_succeeds_and_is_verified(self) -> None:
        primary = FakeProvider(_spec("local:primary", local=True, competence=0.4))
        inventory = DeploymentInventory()
        inventory.register(_profile())
        runtime, work_id, result = self._run(inventory, self._router(primary))
        self.assertEqual(result.status, "completed")
        execution = runtime.store.list_executions(work_id)[0]
        self.assertEqual(execution.status, "pass")
        self.assertEqual(execution.provider, "local:primary")
        self.assertEqual(len(primary.calls), 1)
        request = primary.calls[0]
        self.assertEqual(request.capability_id, "reasoning.general")
        self.assertEqual(request.metadata["work_id"], work_id)
        self.assertNotIn("task_id", request.metadata)
        kinds = {item.kind for item in runtime.store.list_artifacts(work_id)}
        self.assertIn("verification_result", kinds)
        self.assertIn("execution_receipt", kinds)
        names = {event.name for event in runtime.store.list_events(work_id)}
        self.assertIn("provider.selected", names)

    def test_ineligible_provider_is_never_selected(self) -> None:
        bait = FakeProvider(_spec("cloud:bait", local=False, competence=0.99))
        primary = FakeProvider(_spec("local:primary", local=True, competence=0.1))
        inventory = DeploymentInventory()
        inventory.register(_profile(eligible_providers=("local:primary",)))
        runtime, work_id, result = self._run(
            inventory, self._router(bait, primary)
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(runtime.store.list_executions(work_id)[0].provider, "local:primary")
        self.assertEqual(bait.calls, [])
        self.assertEqual(len(primary.calls), 1)

    def test_post_accept_provider_addition_cannot_widen_work(self) -> None:
        primary = FakeProvider(_spec("local:primary", local=True, competence=0.2))
        extra = FakeProvider(_spec("cloud:extra", local=False, competence=0.99))
        inventory = DeploymentInventory()
        inventory.register(_profile(eligible_providers=("local:primary",)))
        runtime = build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            model_router=self._router(primary),
        )
        work_id = runtime.accept(_brief(), "interpret")
        runtime._engine.model_consumer._router.registry.register(extra)
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(runtime.store.list_executions(work_id)[0].provider, "local:primary")
        self.assertEqual(extra.calls, [])

    def test_empty_eligible_providers_fail_closed(self) -> None:
        primary = FakeProvider(_spec("local:primary", local=True, competence=0.9))
        inventory = DeploymentInventory()
        inventory.register(_profile(eligible_providers=()))
        runtime, work_id, result = self._run(inventory, self._router(primary))
        self.assertEqual(result.status, "failed")
        execution = runtime.store.list_executions(work_id)[0]
        self.assertEqual(execution.status, "fail")
        self.assertIn("no eligible provider", execution.error or "")
        self.assertEqual(primary.calls, [])

    def test_privacy_blocks_disallowed_provider(self) -> None:
        cloud = FakeProvider(_spec("cloud:xai", local=False, competence=0.9))
        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                eligible_providers=("cloud:xai",),
                privacy="local_only",
            )
        )
        runtime, work_id, result = self._run(inventory, self._router(cloud))
        self.assertEqual(result.status, "failed")
        self.assertIn("no eligible provider", runtime.store.list_executions(work_id)[0].error or "")
        self.assertEqual(cloud.calls, [])

    def test_sensitive_classification_requires_local_provider(self) -> None:
        cloud = FakeProvider(_spec("cloud:xai", local=False, competence=0.9))
        local = FakeProvider(_spec("local:primary", local=True, competence=0.2))
        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                eligible_providers=("cloud:xai", "local:primary"),
                privacy="cloud_allowed",
                data_classification="sensitive",
            )
        )
        runtime, work_id, result = self._run(inventory, self._router(cloud, local))
        self.assertEqual(result.status, "completed")
        self.assertEqual(runtime.store.list_executions(work_id)[0].provider, "local:primary")
        self.assertEqual(cloud.calls, [])

    def test_primary_unavailable_falls_back_to_eligible_provider(self) -> None:
        primary = FakeProvider(
            _spec("local:primary", local=True, competence=0.9),
            error=RuntimeError("primary down"),
        )
        backup = FakeProvider(_spec("local:backup", local=True, competence=0.4))
        inventory = DeploymentInventory()
        inventory.register(
            _profile(eligible_providers=("local:primary", "local:backup"))
        )
        runtime, work_id, result = self._run(
            inventory, self._router(primary, backup)
        )
        self.assertEqual(result.status, "completed")
        executions = runtime.store.list_executions(work_id)
        self.assertEqual(len(executions), 2)
        self.assertEqual(executions[0].status, "abstain")
        self.assertEqual(executions[0].provider, "local:primary")
        self.assertEqual(executions[1].status, "pass")
        self.assertEqual(executions[1].provider, "local:backup")
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(backup.calls), 1)

    def test_disabled_primary_is_skipped_without_calling_it(self) -> None:
        primary = FakeProvider(
            _spec("local:primary", local=True, competence=0.9, enabled=False)
        )
        backup = FakeProvider(_spec("local:backup", local=True, competence=0.1))
        inventory = DeploymentInventory()
        inventory.register(
            _profile(eligible_providers=("local:primary", "local:backup"))
        )
        runtime, work_id, result = self._run(
            inventory, self._router(primary, backup)
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(runtime.store.list_executions(work_id)[0].provider, "local:backup")
        self.assertEqual(primary.calls, [])

    def test_budget_ceiling_is_enforced_before_dispatch(self) -> None:
        expensive = FakeProvider(
            _spec(
                "local:primary",
                local=True,
                competence=0.5,
                input_cost_per_million=1_000_000.0,
                output_cost_per_million=1_000_000.0,
            )
        )
        inventory = DeploymentInventory()
        inventory.register(
            _profile(budget=ExecutionBudget(max_attempts=1, max_cost_usd=0.0000001))
        )
        runtime, work_id, result = self._run(inventory, self._router(expensive))
        self.assertEqual(result.status, "failed")
        execution = runtime.store.list_executions(work_id)[0]
        self.assertEqual(execution.status, "fail")
        self.assertIn("exceeds capability budget", execution.error or "")
        self.assertEqual(expensive.calls, [])

    def test_work_model_call_budget_is_enforced(self) -> None:
        primary = FakeProvider(_spec("local:primary", local=True, competence=0.5))
        inventory = DeploymentInventory()
        inventory.register(_profile())
        runtime, work_id, result = self._run(
            inventory,
            self._router(primary),
            budget=RuntimeBudget(max_model_calls=0),
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("model-call budget", runtime.store.list_executions(work_id)[0].error or "")
        self.assertEqual(primary.calls, [])

    def test_model_output_is_verified_before_completion(self) -> None:
        primary = FakeProvider(
            _spec("local:primary", local=True, competence=0.5),
            text="   ",
        )
        inventory = DeploymentInventory()
        inventory.register(_profile(budget=ExecutionBudget(max_attempts=1)))
        runtime, work_id, result = self._run(inventory, self._router(primary))
        self.assertEqual(result.status, "failed")
        execution = runtime.store.list_executions(work_id)[0]
        self.assertEqual(execution.status, "rework")
        self.assertIn("no usable output", execution.error or "")
        self.assertEqual(len(primary.calls), 1)

    def test_model_cannot_invoke_gateway_tools(self) -> None:
        calls: list[str] = []

        def bait(arguments):
            calls.append("bait")
            return ToolResult(True, output=arguments, receipt={"ok": True})

        gateway = ToolGateway()
        gateway.register(ToolDescriptor(id="bait.tool", description="Bait"), bait)
        primary = FakeProvider(_spec("local:primary", local=True, competence=0.5))
        inventory = DeploymentInventory()
        inventory.register(_profile())
        runtime, work_id, result = self._run(
            inventory, self._router(primary), gateway=gateway
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(calls, [])
        request = primary.calls[0]
        self.assertNotIn("bait.tool", request.input)
        pack_tools = __import__("json").loads(request.input).get("tools") or []
        self.assertEqual(pack_tools, [])

    def test_deterministic_path_unchanged_when_router_is_present(self) -> None:
        from atlas_core.capabilities import CapabilityOutcome

        def handler(request):
            return CapabilityOutcome(
                "pass",
                output={"ok": True},
                receipt={"ok": True},
            )

        primary = FakeProvider(_spec("local:primary", local=True, competence=0.9))
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                executor_kind="deterministic",
                verifier_id="core.nonempty",
            ),
            handler,
        )
        runtime = build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            model_router=self._router(primary),
        )
        work_id = runtime.accept(
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(primary.calls, [])
        self.assertIsNone(runtime.store.list_executions(work_id)[0].provider)

    def test_source_does_not_scan_live_registry_for_unnamed_keys(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "atlas_core"
            / "work"
            / "model.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("router.select(", source)
        self.assertIn("provider_snapshots", source)
        self.assertNotIn("TaskRuntime", source)

    def test_same_key_model_change_after_accept_is_rejected(self) -> None:
        primary = FakeProvider(_spec("local:primary", local=True, competence=0.4))
        inventory = DeploymentInventory()
        inventory.register(_profile())
        runtime = build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            model_router=self._router(primary),
        )
        work_id = runtime.accept(_brief(), "interpret")
        pin = runtime.contract(work_id).capability("reasoning.general")
        self.assertEqual(len(pin.provider_snapshots), 1)
        self.assertEqual(pin.provider_snapshots[0].model, "model-local:primary")
        changed = FakeProvider(
            _spec("local:primary", local=True, competence=0.4, model="other-model")
        )
        runtime._engine.model_consumer._router.registry._providers["local:primary"] = (
            changed
        )
        result = runtime.run(work_id)
        self.assertEqual(result.status, "failed")
        self.assertIn(
            "does not match the frozen pin",
            runtime.store.list_executions(work_id)[0].error or "",
        )
        self.assertEqual(changed.calls, [])
        self.assertEqual(primary.calls, [])

    def test_live_ranking_may_change_among_frozen_snapshots(self) -> None:
        first = FakeProvider(_spec("local:primary", local=True, competence=0.9))
        second = FakeProvider(_spec("local:backup", local=True, competence=0.2))
        inventory = DeploymentInventory()
        inventory.register(
            _profile(eligible_providers=("local:primary", "local:backup"))
        )
        runtime = build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            model_router=self._router(first, second),
        )
        work_id = runtime.accept(_brief(), "interpret")
        runtime._engine.model_consumer._router.record_eval_score(
            "local:backup", "reasoning.general", 0.99
        )
        runtime._engine.model_consumer._router.record_eval_score(
            "local:primary", "reasoning.general", 0.1
        )
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            runtime.store.list_executions(work_id)[0].provider, "local:backup"
        )
        self.assertEqual(first.calls, [])
        self.assertEqual(len(second.calls), 1)

    def test_named_provider_without_inventory_is_unarmed(self) -> None:
        from atlas_core.work import compile_contract

        inventory = DeploymentInventory()
        inventory.register(_profile(eligible_providers=("local:primary",)))
        contract = compile_contract(
            work_id="work_1",
            brief=_brief(),
            authority_scope="interpret",
            inventory=inventory,
        )
        self.assertFalse(contract.capability("reasoning.general").armed)

    def test_provider_registry_cannot_replace_in_place(self) -> None:
        from atlas_core.providers import ProviderRegistry, ProviderRegistryError

        registry = ProviderRegistry()
        registry.register(FakeProvider(_spec("local:primary", local=True, competence=0.5)))
        with self.assertRaises(ProviderRegistryError):
            registry.register(
                FakeProvider(_spec("local:primary", local=True, competence=0.1))
            )
        source = (
            Path(__file__).resolve().parents[1]
            / "atlas_core"
            / "providers"
            / "registry.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("replace", source)


if __name__ == "__main__":
    unittest.main()
