from __future__ import annotations

import json
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from typing import Callable

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import ContextPolicy, ExecutionBudget
from atlas_core.context import ContextManifest, ContextPack, ManifestItem
from atlas_core.providers import ModelResponse, ModelRouter, ProviderRegistry, ProviderSpec
from atlas_core.runtime_types import RuntimeBudget
from atlas_core.tools import ToolDescriptor, ToolGateway, ToolResult
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    build_work_runtime,
)
from atlas_core.work.model import _normalize_claim_bearing_output


class FakeProvider:
    def __init__(
        self,
        spec: ProviderSpec,
        *,
        text: str | Callable = "A bounded explanation of the request.",
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
        text = self.text(request) if callable(self.text) else self.text
        return ModelResponse(
            text,
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


def _brief(**overrides) -> TaskBrief:
    payload = dict(
        objective="Explain the request",
        capabilities=("reasoning.general",),
        required_authority="interpret",
        expected_effect="A bounded explanation",
    )
    payload.update(overrides)
    return TaskBrief(**payload)


def _claim_envelope(request, *, kind="retrieved", evidence=True) -> str:
    payload = json.loads(request.input)
    artifact_id = payload["artifacts"][0]["id"] if evidence else None
    return json.dumps(
        {
            "deliverable": "A bounded explanation grounded in the supplied record.",
            "claims": [
                {
                    "kind": kind,
                    "subject": "maintenance record",
                    "statement": "The supplied record reports the observed condition.",
                    "evidence_artifact_ids": [] if artifact_id is None else [artifact_id],
                }
            ],
            "limitations": ["Only the supplied record was considered."],
        }
    )


def _claim_envelope_from_ids(
    artifact_id: str | None,
    *,
    kind: str = "retrieved",
) -> str:
    return json.dumps(
        {
            "deliverable": "A bounded conclusion.",
            "claims": [
                {
                    "kind": kind,
                    "subject": "record",
                    "statement": "The record supports this conclusion.",
                    "evidence_artifact_ids": [] if artifact_id is None else [artifact_id],
                }
            ],
            "limitations": ["bounded"],
        }
    )


def _pack(
    work_id: str,
    *included: ManifestItem,
    omitted: tuple[str, ...] = (),
    dropped: tuple[ManifestItem, ...] = (),
) -> ContextPack:
    return ContextPack(
        payload={},
        chars=0,
        tokens=0,
        omitted_artifact_ids=omitted,
        manifest=ContextManifest(
            manifest_id="context_test",
            work_id=work_id,
            step_id="step_test",
            execution_id="execution_test",
            capability_id="reasoning.general",
            capability_version="1",
            assembled_at="2026-01-01T00:00:00Z",
            assembler_version="test",
            budget_tokens=128,
            total_tokens=1,
            included=tuple(included),
            dropped=dropped,
            buckets={},
            token_accounting={},
        ),
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

    def _run(
        self,
        inventory,
        router,
        *,
        gateway=None,
        budget=None,
        brief=None,
        inputs=None,
    ):
        runtime = build_work_runtime(
            db_path=self.db,
            profiles=inventory,
            tool_gateway=gateway,
            model_router=router,
            budget=budget,
        )
        work_id = runtime.accept(brief or _brief(), "interpret", inputs=inputs)
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
        from tests.work_helpers import run_with_confirmation

        result = run_with_confirmation(runtime, work_id)
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

    def test_deliverable_only_model_output_remains_plain_prose_without_claims(self) -> None:
        provider = FakeProvider(_spec("local:primary", local=True, competence=0.4))
        inventory = DeploymentInventory()
        inventory.register(_profile(model_outcome_policy="deliverable_only"))
        runtime, work_id, result = self._run(inventory, self._router(provider))
        self.assertEqual(result.status, "completed")
        self.assertEqual(runtime.store.list_claims(work_id), ())

    def test_claim_bearing_model_rejects_prose_only_output(self) -> None:
        provider = FakeProvider(
            _spec("local:primary", local=True, competence=0.4),
            text="A plausible but unstructured explanation.",
        )
        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                model_outcome_policy="claim_bearing",
                budget=ExecutionBudget(max_attempts=1),
            )
        )
        runtime, work_id, result = self._run(inventory, self._router(provider))
        self.assertEqual(result.status, "failed")
        execution = runtime.store.list_executions(work_id)[0]
        self.assertEqual(execution.status, "rework")
        self.assertIn("not valid JSON", execution.error or "")
        self.assertEqual(runtime.store.list_claims(work_id), ())

    def test_claim_bearing_model_persists_valid_full_context_evidence(self) -> None:
        provider = FakeProvider(
            _spec("local:primary", local=True, competence=0.4),
            text=_claim_envelope,
        )
        inventory = DeploymentInventory()
        inventory.register(_profile(model_outcome_policy="claim_bearing"))
        runtime, work_id, result = self._run(
            inventory,
            self._router(provider),
            inputs={"reasoning.general": {"record": "pump A was inspected"}},
        )
        self.assertEqual(result.status, "completed")
        claim = runtime.store.list_claims(work_id)[0]
        self.assertEqual(claim.kind, "retrieved")
        self.assertEqual(claim.subject, "maintenance record")
        self.assertEqual(len(claim.evidence_artifact_ids), 1)
        input_artifact = runtime.store.list_executions(work_id)[0].input_artifact_ids[0]
        self.assertEqual(claim.evidence_artifact_ids, (input_artifact,))
        receipt = runtime.store.list_executions(work_id)[0].receipt
        self.assertEqual(receipt["model_output_limitations"], ["Only the supplied record was considered."])

    def test_claim_bearing_model_rejects_reference_only_context_artifact(self) -> None:
        provider = FakeProvider(
            _spec("local:primary", local=True, competence=0.4),
            text=_claim_envelope,
        )
        inventory = DeploymentInventory()
        inventory.register(
            _profile(
                model_outcome_policy="claim_bearing",
                context_policy=ContextPolicy(allow_full_artifact=False),
                budget=ExecutionBudget(max_attempts=1),
            )
        )
        runtime, work_id, result = self._run(
            inventory,
            self._router(provider),
            inputs={"reasoning.general": {"record": "reference only"}},
        )
        self.assertEqual(result.status, "failed")
        self.assertIn(
            "without full context exposure",
            runtime.store.list_executions(work_id)[0].error or "",
        )

    def test_claim_envelope_rejects_invalid_or_unavailable_evidence(self) -> None:
        from atlas_core.work import WorkStore

        store = WorkStore(self.db)
        store.initialize()
        work = store.create_work(objective="one", success_criteria=("one",))
        other = store.create_work(objective="two", success_criteria=("two",))
        full = store.put_artifact(work.id, kind="source", payload="source")
        foreign = store.put_artifact(other.id, kind="source", payload="other")
        full_item = ManifestItem(full.id, "artifact", "source", 1, representation="full")
        before_output = _pack(work.id, full_item)
        generated_output = store.put_artifact(
            work.id, kind="capability_result", payload="newly generated"
        )
        valid = _claim_envelope_from_ids(full.id)
        output, claims, limitations = _normalize_claim_bearing_output(
            valid, work_id=work.id, pack=_pack(work.id, full_item), store=store
        )
        self.assertTrue(output)
        self.assertEqual(claims[0]["evidence_artifact_ids"], (full.id,))
        self.assertEqual(limitations, ("bounded",))

        for artifact_id, item, expected in (
            ("artifact_fabricated", full_item, "without full context exposure"),
            (foreign.id, ManifestItem(foreign.id, "artifact", "source", 1, representation="full"), "another work"),
            (full.id, ManifestItem(full.id, "artifact", "source", 1, representation="reference"), "without full context exposure"),
            (generated_output.id, full_item, "without full context exposure"),
        ):
            with self.subTest(artifact_id=artifact_id, representation=item.representation):
                with self.assertRaisesRegex(ValueError, expected):
                    _normalize_claim_bearing_output(
                        _claim_envelope_from_ids(artifact_id),
                        work_id=work.id,
                        pack=_pack(work.id, item),
                        store=store,
                    )

        for unavailable_pack in (
            _pack(work.id, omitted=(full.id,)),
            _pack(
                work.id,
                dropped=(
                    ManifestItem(
                        full.id, "artifact", "source", 1, representation="dropped"
                    ),
                ),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "without full context exposure"):
                _normalize_claim_bearing_output(
                    _claim_envelope_from_ids(full.id),
                    work_id=work.id,
                    pack=unavailable_pack,
                    store=store,
                )
        with self.assertRaisesRegex(ValueError, "without full context exposure"):
            _normalize_claim_bearing_output(
                _claim_envelope_from_ids(generated_output.id),
                work_id=work.id,
                pack=before_output,
                store=store,
            )

    def test_claim_linkage_is_authorized_by_exact_pin_and_persisted_manifest(self) -> None:
        from atlas_core.work import WorkStore, compile_contract

        store = WorkStore(self.db)
        store.initialize()
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="reasoning.general",
                executor_kind="model",
                model_outcome_policy="claim_bearing",
                verifier_id="core.nonempty",
            )
        )
        brief = _brief(completion_grounding_policy="evidence_required")
        contract = compile_contract(
            work_id="work_manifest_linkage",
            brief=brief,
            authority_scope="interpret",
            inventory=inventory,
        )
        store.create_work(
            work_id=contract.work_id,
            objective=brief.objective,
            success_criteria=contract.success_criteria,
            criterion_specs=contract.criteria,
        )
        step = store.add_step(
            contract.work_id,
            description="reason",
            capability="reasoning.general",
            capability_version="1.0.0",
            contract_capability_ordinal=1,
        )
        source = store.put_artifact(contract.work_id, kind="source", payload="source")
        execution = store.begin_execution(
            contract.work_id,
            step_id=step.id,
            capability="reasoning.general",
            capability_version="1.0.0",
            input_artifact_ids=(source.id,),
        )
        base = _pack(
            contract.work_id,
            ManifestItem(source.id, "artifact", "source", 1, representation="full"),
        )
        manifest = replace(
            base.manifest,
            step_id=step.id,
            execution_id=execution.id,
            capability_version="1.0.0",
        )
        pack = replace(base, manifest=manifest)
        store.write_context_manifest(
            contract.work_id,
            step_id=step.id,
            execution_id=execution.id,
            capability="reasoning.general",
            capability_version="1.0.0",
            assembler_version="test",
            budget_tokens=128,
            total_tokens=1,
            manifest=manifest.as_dict(),
            manifest_id=manifest.manifest_id,
        )
        envelope = json.loads(_claim_envelope_from_ids(source.id))
        envelope["claims"][0]["criterion_ordinals"] = [1]
        _output, claims, _limitations = _normalize_claim_bearing_output(
            json.dumps(envelope), work_id=contract.work_id, pack=pack, store=store,
            contract=contract, execution_id=execution.id,
        )
        self.assertEqual(claims[0]["criterion_ordinals"], (1,))
        with self.assertRaisesRegex(ValueError, "not persisted for this execution"):
            _normalize_claim_bearing_output(
                json.dumps(envelope), work_id=contract.work_id, pack=pack, store=store,
                contract=contract, execution_id="execution_wrong",
            )

    def test_claim_envelope_requires_evidence_and_preserves_optional_claims(self) -> None:
        from atlas_core.work import WorkStore

        store = WorkStore(self.db)
        store.initialize()
        work = store.create_work(objective="one", success_criteria=("one",))
        pack = _pack(work.id)
        with self.assertRaisesRegex(ValueError, "require evidence"):
            _normalize_claim_bearing_output(
                _claim_envelope_from_ids(None, kind="retrieved"),
                work_id=work.id,
                pack=pack,
                store=store,
            )
        for kind in ("inferred", "suggested"):
            with self.subTest(kind=kind):
                _output, claims, _limitations = _normalize_claim_bearing_output(
                    _claim_envelope_from_ids(None, kind=kind),
                    work_id=work.id,
                    pack=pack,
                    store=store,
                )
                self.assertEqual(claims[0]["evidence_artifact_ids"], ())

    def test_claim_envelope_fails_closed_for_invalid_structure(self) -> None:
        from atlas_core.work import WorkStore

        store = WorkStore(self.db)
        store.initialize()
        work = store.create_work(objective="one", success_criteria=("one",))
        source = store.put_artifact(work.id, kind="source", payload="source")
        pack = _pack(
            work.id,
            ManifestItem(source.id, "artifact", "source", 1, representation="full"),
        )
        invalid = (
            "not json",
            json.dumps({"deliverable": "x", "claims": [], "limitations": [], "extra": True}),
            json.dumps(
                {
                    "deliverable": "x",
                    "claims": [
                        {
                            "kind": "unknown",
                            "subject": "subject",
                            "statement": "statement",
                            "evidence_artifact_ids": [source.id],
                        }
                    ],
                    "limitations": [],
                }
            ),
            _claim_envelope_from_ids(source.id).replace(
                f'"{source.id}"', f'"{source.id}", "{source.id}"'
            ),
        )
        for response in invalid:
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    _normalize_claim_bearing_output(
                        response, work_id=work.id, pack=pack, store=store
                    )


if __name__ == "__main__":
    unittest.main()
