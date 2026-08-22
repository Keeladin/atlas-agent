from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from starlette.testclient import TestClient

from atlas_api.app import create_app
from atlas_api.auth import AuthService, CookiePolicy
from atlas_api.compose import build_default_work_runtime, compose_services
from atlas_companion.credentials import CredentialStore
from atlas_core.advanced import TaskBrief
from atlas_core.providers import (
    ModelResponse,
    ModelRouter,
    ProviderSpec,
)
from atlas_core.capabilities import register_intelligence_capabilities
from atlas_core.work import (
    DeploymentInventory,
    UnavailableWork,
    build_work_runtime,
)


HOST_TEXT_INTELLIGENCE_IDS = (
    "planning.general",
    "reasoning.general",
    "generation.compose",
    "reasoning.deep_analysis",
    "coding.software_engineering",
)


class FakeProvider:
    def __init__(
        self,
        spec: ProviderSpec,
        *,
        base_url: str | None = None,
        text: str = "A bounded explanation of the request.",
        error: Exception | None = None,
    ) -> None:
        self.spec = spec
        self.base_url = base_url
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


def _xai_overlay(
    *,
    model: str = "grok-4-latest",
    enabled: bool = True,
    extra: dict | None = None,
) -> dict:
    providers = {
        "xai:expert": {
            "kind": "openai_compatible_chat",
            "model": model,
            "base_url": "https://api.x.ai",
            "api_key_env": "XAI_API_KEY",
            "enabled": enabled,
            "local": False,
            "priority": 50,
            "latency_rank": 35,
            "capabilities": {"conversation.reply": 1.0, "advanced.brief": 1.0},
        }
    }
    if extra:
        providers.update(extra)
    return {"providers": providers}


def _brief(capability_id: str = "reasoning.general") -> TaskBrief:
    return TaskBrief(
        objective="Explain the request",
        capabilities=(capability_id,),
        required_authority="interpret",
        expected_effect="A bounded explanation",
    )


def _stub_generate(
    provider,
    text: str = json.dumps(
        {
            "deliverable": "A bounded explanation of the request.",
            "claims": [],
            "limitations": [],
        }
    ),
):
    calls: list = []

    def generate(request):
        calls.append(request)
        return ModelResponse(
            text,
            provider.spec.key,
            provider.spec.model,
            raw={},
            metrics={"input_tokens": 10, "output_tokens": 8},
        )

    generate.calls = calls
    provider.generate = generate
    return generate


class AtlasApiSharedProviderTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work_db = self.root / "atlas-work.db"
        self.chat_db = self.root / "atlas-chat.db"
        self.overlay = self.root / "runtime-providers.companion.json"
        self.saved_key = os.environ.pop("XAI_API_KEY", None)
        self.auth = AuthService(
            password="test-password",
            secret="test-session-secret-with-enough-entropy",
            cookie_policy=CookiePolicy(
                secure=False, samesite="lax", mode="explicit_insecure_dev"
            ),
        )

    def tearDown(self) -> None:
        if self.saved_key is None:
            os.environ.pop("XAI_API_KEY", None)
        else:
            os.environ["XAI_API_KEY"] = self.saved_key
        self.tmp.cleanup()

    def _compose(self, overlay: dict | None = None):
        payload = overlay if overlay is not None else _xai_overlay()
        self.overlay.write_text(json.dumps(payload), encoding="utf-8")
        CredentialStore(self.root).put("xai:expert", "store-secret-value")
        return compose_services(
            work_db=self.work_db,
            chat_db=self.chat_db,
            provider_config=self.overlay,
            auth=self.auth,
        )

    def test_chat_advanced_and_work_share_the_effective_registry(self) -> None:
        services = self._compose()
        registry = services.work._providers
        self.assertIsNotNone(registry)
        self.assertIs(
            services.work._engine.model_consumer._router.registry, registry
        )
        chat_provider = services.chat._provider
        self.assertIsNot(registry, chat_provider)
        self.assertIs(chat_provider, registry.get(chat_provider.spec.key))
        self.assertIs(services.advanced._provider, chat_provider)
        self.assertEqual(chat_provider.spec.key, "xai:expert")

    def test_eligibility_is_host_registry_keys_not_capability_scores(self) -> None:
        services = self._compose()
        profile = services.work._profiles.get("reasoning.general")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.eligible_providers, ("xai:expert",))
        self.assertIsNone(services.chat._provider.spec.score_for("reasoning.general"))
        for capability_id in HOST_TEXT_INTELLIGENCE_IDS:
            item = services.work._profiles.get(capability_id)
            self.assertIsNotNone(item, capability_id)
            self.assertEqual(item.executor_kind, "model")
            self.assertEqual(item.eligible_providers, ("xai:expert",))
        self.assertIsNone(services.work._profiles.get("documents.multimodal"))

    def test_documents_multimodal_is_not_auto_registered_from_host_keys(self) -> None:
        services = self._compose()
        self.assertIsNone(services.work._profiles.get("documents.multimodal"))
        with self.assertRaises(UnavailableWork) as ctx:
            services.work.accept(
                TaskBrief(
                    objective="Interpret the attached document pages",
                    capabilities=("documents.multimodal",),
                    required_authority="interpret",
                    expected_effect="A bounded reading of the document",
                ),
                "interpret",
            )
        self.assertEqual(ctx.exception.result.unarmed, ("documents.multimodal",))
        self.assertEqual(services.work.store.list_work(), ())

    def test_documents_multimodal_overlay_score_is_not_eligibility(self) -> None:
        overlay = _xai_overlay()
        overlay["providers"]["xai:expert"]["capabilities"] = {
            "conversation.reply": 1.0,
            "advanced.brief": 1.0,
            "documents.multimodal": 1.0,
        }
        overlay["providers"]["xai:expert"]["metadata"] = {
            "score_source": "neutral_seed_until_atlas_evals"
        }
        services = self._compose(overlay)
        self.assertEqual(
            services.chat._provider.spec.score_for("documents.multimodal"),
            1.0,
        )
        self.assertIsNone(services.work._profiles.get("documents.multimodal"))
        with self.assertRaises(UnavailableWork):
            services.work.accept(
                TaskBrief(
                    objective="Interpret the attached document pages",
                    capabilities=("documents.multimodal",),
                    required_authority="interpret",
                    expected_effect="A bounded reading of the document",
                ),
                "interpret",
            )

    def test_disabled_host_provider_is_still_named_eligible(self) -> None:
        overlay = _xai_overlay(
            extra={
                "cloud:bait": {
                    "kind": "openai_compatible_chat",
                    "model": "bait-model",
                    "base_url": "https://bait.example.invalid",
                    "enabled": False,
                    "local": False,
                    "priority": 99,
                    "latency_rank": 1,
                    "capabilities": {"reasoning.general": 1.0},
                }
            }
        )
        services = self._compose(overlay)
        profile = services.work._profiles.get("reasoning.general")
        self.assertEqual(
            tuple(sorted(profile.eligible_providers)),
            ("cloud:bait", "xai:expert"),
        )

    def test_enabled_xai_provider_is_pinned_for_model_backed_work(self) -> None:
        services = self._compose()
        work_id = services.work.accept(_brief(), "interpret")
        pin = services.work.contract(work_id).capability("reasoning.general")
        self.assertTrue(pin.armed)
        self.assertEqual(pin.eligible_providers, ("xai:expert",))
        self.assertEqual(len(pin.provider_snapshots), 1)
        snapshot = pin.provider_snapshots[0]
        self.assertEqual(snapshot.key, "xai:expert")
        self.assertEqual(snapshot.model, "grok-4-latest")
        self.assertEqual(snapshot.base_url, "https://api.x.ai")
        self.assertFalse(snapshot.local)
        stub = _stub_generate(services.chat._provider)
        result = services.work.run(work_id)
        self.assertEqual(result.status, "completed")
        execution = services.work.store.list_executions(work_id)[0]
        self.assertEqual(execution.status, "pass")
        self.assertEqual(execution.provider, "xai:expert")
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(stub.calls[0].capability_id, "reasoning.general")

    def test_ineligible_provider_cannot_be_used(self) -> None:
        services = self._compose()
        bait = FakeProvider(
            ProviderSpec(
                key="cloud:bait",
                model="bait-model",
                provider_kind="openai_compatible_chat",
                capabilities={"reasoning.general": 1.0},
                local=False,
                enabled=True,
                priority=99,
                latency_rank=1,
            ),
            base_url="https://bait.example.invalid",
        )
        work_id = services.work.accept(_brief(), "interpret")
        services.work._providers.register(bait)
        stub = _stub_generate(services.chat._provider)
        result = services.work.run(work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(services.work.store.list_executions(work_id)[0].provider, "xai:expert")
        self.assertEqual(bait.calls, [])
        self.assertEqual(len(stub.calls), 1)
        pin = services.work.contract(work_id).capability("reasoning.general")
        self.assertEqual(tuple(item.key for item in pin.provider_snapshots), ("xai:expert",))

    def test_missing_router_refuses_intelligence_work(self) -> None:
        runtime = build_default_work_runtime(db_path=self.work_db)
        self.assertIsNone(runtime._providers)
        self.assertIsNone(runtime._engine.model_consumer)
        with self.assertRaises(UnavailableWork) as ctx:
            runtime.accept(_brief(), "interpret")
        self.assertEqual(ctx.exception.result.unarmed, ("reasoning.general",))
        self.assertEqual(runtime.store.list_work(), ())

    def test_named_eligible_keys_without_provider_inventory_are_unarmed(self) -> None:
        inventory = DeploymentInventory()
        register_intelligence_capabilities(inventory, eligible_providers=("xai:expert",))
        runtime = build_work_runtime(db_path=self.work_db, profiles=inventory)
        with self.assertRaises(UnavailableWork) as ctx:
            runtime.accept(_brief(), "interpret")
        self.assertEqual(ctx.exception.result.unarmed, ("reasoning.general",))

    def test_provider_state_model_change_after_accept_invalidates_pin(self) -> None:
        services = self._compose()
        work_id = services.work.accept(_brief(), "interpret")
        live = services.work._providers.get("xai:expert")
        changed = FakeProvider(
            replace(live.spec, model="other-model"),
            base_url=getattr(live, "base_url", None),
        )
        services.work._providers._providers["xai:expert"] = changed
        result = services.work.run(work_id)
        self.assertEqual(result.status, "failed")
        self.assertIn(
            "does not match the frozen pin",
            services.work.store.list_executions(work_id)[0].error or "",
        )
        self.assertEqual(changed.calls, [])
        pin = services.work.contract(work_id).capability("reasoning.general")
        self.assertEqual(pin.provider_snapshots[0].model, "grok-4-latest")

    def test_post_accept_registry_additions_cannot_widen_the_contract(self) -> None:
        services = self._compose()
        extra = FakeProvider(
            ProviderSpec(
                key="cloud:extra",
                model="extra-model",
                provider_kind="openai_compatible_chat",
                capabilities={"reasoning.general": 1.0},
                local=False,
                enabled=True,
                priority=99,
            )
        )
        work_id = services.work.accept(_brief(), "interpret")
        sha = services.work.contract(work_id).sha256
        services.work._providers.register(extra)
        later = services.work.contract(work_id)
        self.assertEqual(later.sha256, sha)
        self.assertEqual(
            tuple(item.key for item in later.capability("reasoning.general").provider_snapshots),
            ("xai:expert",),
        )
        _stub_generate(services.chat._provider)
        services.work.run(work_id)
        self.assertEqual(extra.calls, [])

    def test_deterministic_work_still_accepts_and_runs(self) -> None:
        services = self._compose()
        work_id = services.work.accept(
            TaskBrief(
                objective="Search local knowledge",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="Retrieved local chunks",
            ),
            "read",
            inputs={"knowledge.search": {"query": "atlas"}},
        )
        result = services.work.run(work_id)
        self.assertEqual(result.status, "completed")
        executions = services.work.store.list_executions(work_id)
        self.assertTrue(executions)
        self.assertEqual(executions[0].status, "pass")
        self.assertIsNone(executions[0].provider)
        pin = services.work.contract(work_id).capability("knowledge.search")
        self.assertTrue(pin.armed)
        self.assertEqual(pin.executor_kind, "deterministic")
        self.assertEqual(pin.eligible_providers, ())
        self.assertEqual(pin.provider_snapshots, ())

    def test_advanced_work_accept_is_not_false_host_unavailability(self) -> None:
        services = self._compose()
        app = create_app(services=services, serve_companion=False)
        client = TestClient(app)
        login = client.post("/api/auth/login", json={"password": "test-password"})
        self.assertEqual(login.status_code, 200)
        csrf = login.json()["csrf_token"]
        accepted = client.post(
            "/api/work",
            json={
                "brief": {
                    "objective": "Explain the request",
                    "capabilities": ["reasoning.general"],
                    "required_authority": "interpret",
                    "expected_effect": "A bounded explanation",
                    "constraints": [],
                },
                "authority_scope": "interpret",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(accepted.status_code, 201, accepted.text)
        body = accepted.json()
        self.assertNotEqual(body.get("status"), "unavailable")
        self.assertIn("work_id", body)
        pin = services.work.contract(body["work_id"]).capability("reasoning.general")
        self.assertTrue(pin.armed)
        self.assertEqual(pin.provider_snapshots[0].key, "xai:expert")

        unavailable = client.post(
            "/api/work",
            json={
                "brief": {
                    "objective": "Interpret the attached document pages",
                    "capabilities": ["documents.multimodal"],
                    "required_authority": "interpret",
                    "expected_effect": "A bounded reading of the document",
                    "constraints": [],
                },
                "authority_scope": "interpret",
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(unavailable.status_code, 409, unavailable.text)
        self.assertEqual(unavailable.json()["status"], "unavailable")
        self.assertEqual(unavailable.json()["unarmed"], ["documents.multimodal"])

    def test_empty_eligibility_stays_fail_closed_even_with_host_router(self) -> None:
        services = self._compose()
        inventory = DeploymentInventory()
        register_intelligence_capabilities(inventory, eligible_providers=())
        runtime = build_work_runtime(
            db_path=self.root / "empty-elig.db",
            profiles=inventory,
            model_router=ModelRouter(services.work._providers),
        )
        work_id = runtime.accept(_brief(), "interpret")
        result = runtime.run(work_id)
        self.assertEqual(result.status, "failed")
        self.assertIn(
            "no eligible provider",
            runtime.store.list_executions(work_id)[0].error or "",
        )
        self.assertEqual(runtime.store.list_executions(work_id)[0].provider, None)

    def test_provider_state_model_is_frozen_into_the_pin(self) -> None:
        self.overlay.write_text(json.dumps(_xai_overlay(model="grok-4-latest")), encoding="utf-8")
        CredentialStore(self.root).put("xai:expert", "store-secret-value")
        (self.root / "provider-state.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "xai:expert": {
                            "enabled": True,
                            "model": "grok-4.6",
                            "verified": True,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        services = compose_services(
            work_db=self.work_db,
            chat_db=self.chat_db,
            provider_config=self.overlay,
            auth=self.auth,
        )
        self.assertEqual(services.chat.turn_provider.model, "grok-4.6")
        work_id = services.work.accept(_brief(), "interpret")
        snapshot = services.work.contract(work_id).capability(
            "reasoning.general"
        ).provider_snapshots[0]
        self.assertEqual(snapshot.model, "grok-4.6")
