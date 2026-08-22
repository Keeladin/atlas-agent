from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from atlas_api.auth import AuthService, CookiePolicy
from atlas_api.compose import compose_services
from atlas_companion.credentials import CredentialStore


def _xai_overlay(*, model: str = "grok-4-latest", enabled: bool = True) -> dict:
    return {
        "providers": {
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
    }


class AtlasApiCredentialStoreTests(unittest.TestCase):
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

    def _compose(self):
        return compose_services(
            work_db=self.work_db,
            chat_db=self.chat_db,
            provider_config=self.overlay,
            auth=self.auth,
        )

    def test_xai_expert_loads_from_credential_store_without_shell_env(self) -> None:
        self.overlay.write_text(json.dumps(_xai_overlay()), encoding="utf-8")
        store = CredentialStore(self.root)
        store.put("xai:expert", "store-secret-value")
        self.assertNotIn("XAI_API_KEY", os.environ)
        self.assertNotIn("store-secret-value", self.overlay.read_text(encoding="utf-8"))

        services = self._compose()

        self.assertEqual(os.environ.get("XAI_API_KEY"), "store-secret-value")
        self.assertEqual(services.chat.turn_provider.provider_key, "xai:expert")
        self.assertEqual(services.chat.turn_provider.model, "grok-4-latest")
        key_path = store.path_for("xai:expert")
        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        dumped = json.dumps(
            {
                "provider_key": services.chat.turn_provider.provider_key,
                "model": services.chat.turn_provider.model,
            }
        )
        self.assertNotIn("store-secret-value", dumped)

    def test_shell_env_is_fallback_when_secret_file_is_absent(self) -> None:
        self.overlay.write_text(json.dumps(_xai_overlay()), encoding="utf-8")
        os.environ["XAI_API_KEY"] = "env-secret-value"
        services = self._compose()
        self.assertEqual(os.environ.get("XAI_API_KEY"), "env-secret-value")
        self.assertEqual(services.chat.turn_provider.provider_key, "xai:expert")
        self.assertFalse(CredentialStore(self.root).configured("xai:expert"))

    def test_provider_state_selected_model_is_applied(self) -> None:
        self.overlay.write_text(
            json.dumps(_xai_overlay(model="grok-4-latest")),
            encoding="utf-8",
        )
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
        services = self._compose()
        self.assertEqual(services.chat.turn_provider.model, "grok-4.6")
        self.assertEqual(services.advanced._provider.spec.model, "grok-4.6")
