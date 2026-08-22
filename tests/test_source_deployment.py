from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from starlette.testclient import TestClient

from atlas_api.app import create_app
from atlas_api.auth import AuthService, CookiePolicy
from atlas_api.compose import compose_services
from atlas_core.__main__ import _work_runtime
from atlas_core.advanced import TaskBrief
from atlas_core.sources import LocalRootConfig, LocalSourceError, load_local_source_deployment
from atlas_core.work import UnavailableWork


class SourceDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        self.root.mkdir()
        (self.root / "hello.txt").write_text("hello atlas\n", encoding="utf-8")
        (self.root / "second.txt").write_text("second file\n", encoding="utf-8")
        (self.root / "subdir").mkdir()
        self.config = self.base / "runtime.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_config(self, *, roots=None) -> None:
        self.config.write_text(json.dumps({
            "providers": {
                "local:test": {
                    "kind": "openai_compatible_chat", "model": "test",
                    "base_url": "http://127.0.0.1:1234", "local": True,
                    "enabled": True, "capabilities": {},
                }
            },
            "local_source_roots": roots if roots is not None else [{
                "provider_namespace": "local", "root_id": "proof",
                "host_path": str(self.root), "display_name": "Proof files",
                "read_allowed": True, "mutation_allowed": False,
                "allow_cross_mounts": False,
            }],
        }), encoding="utf-8")

    def test_loads_roots_with_deterministic_security_revision_and_safe_state(self) -> None:
        self.write_config()
        first = load_local_source_deployment(self.config)
        second = load_local_source_deployment(self.config)
        try:
            state = first.public_state()
            self.assertEqual(state, second.public_state())
            self.assertEqual(state[0]["root_id"], "proof")
            self.assertTrue(state[0]["configuration_revision"].startswith("local-root-v1-"))
            self.assertNotIn(str(self.root), json.dumps(state))
            self.assertNotIn("host_path", state[0])
        finally:
            first.close()
            second.close()

    def test_security_policy_change_changes_configuration_revision(self) -> None:
        self.write_config()
        first = load_local_source_deployment(self.config)
        first_revision = first.public_state()[0]["configuration_revision"]
        first.close()
        self.write_config(roots=[{
            "provider_namespace": "local", "root_id": "proof",
            "host_path": str(self.root), "display_name": "Proof files",
            "read_allowed": True, "mutation_allowed": True,
            "allow_cross_mounts": False,
        }])
        second = load_local_source_deployment(self.config)
        try:
            self.assertNotEqual(
                first_revision,
                second.public_state()[0]["configuration_revision"],
            )
        finally:
            second.close()

    def test_invalid_root_fails_closed_and_no_roots_register_no_files(self) -> None:
        self.write_config(roots=[{
            "provider_namespace": "local", "root_id": "bad",
            "host_path": "relative/path",
        }])
        with self.assertRaisesRegex(ValueError, "must be absolute"):
            load_local_source_deployment(self.config)

        self.write_config(roots=[])
        deployment = load_local_source_deployment(self.config)
        try:
            runtime = _work_runtime(
                self.base / "empty.db", morning=False,
                local_source_registry=deployment.registry,
                local_source_kernel=deployment.kernel,
            )
            with self.assertRaises(UnavailableWork):
                runtime.accept(
                    TaskBrief("List files", ("files.list",), "read", "List files"),
                    "read", inputs={"files.list": {}},
                )
        finally:
            deployment.close()

    def test_cli_and_api_composition_share_loaded_registry_and_close_descriptors(self) -> None:
        self.write_config()
        cli_runtime = _work_runtime(
            self.base / "cli.db", morning=False, deployment_config=self.config,
        )
        self.assertIsNotNone(cli_runtime._profiles.get("files.read"))
        cli_runtime._owned_local_source_deployment.close()

        auth = AuthService(
            password="test", secret="test-secret",
            cookie_policy=CookiePolicy(False, "lax", "test"),
        )
        services = compose_services(
            work_db=self.base / "work.db", chat_db=self.base / "chat.db",
            provider_config=self.config, auth=auth,
        )
        self.assertIs(services.work.local_source_registry, services.local_sources.registry)
        self.assertIsNotNone(services.work._profiles.get("files.list"))
        revision = services.local_sources.public_state()[0]["configuration_revision"]
        services.close()
        with self.assertRaises(LocalSourceError) as ctx:
            services.work.local_source_registry.get(
                "local", "proof", configuration_revision=revision,
            )
        self.assertEqual(ctx.exception.code, "root_unknown")

    def test_api_health_exposes_only_safe_operator_root_state(self) -> None:
        self.write_config()
        auth = AuthService(
            password="test", secret="test-secret",
            cookie_policy=CookiePolicy(False, "lax", "test"),
        )
        services = compose_services(
            work_db=self.base / "health-work.db",
            chat_db=self.base / "health-chat.db",
            provider_config=self.config,
            auth=auth,
        )
        with TestClient(create_app(services=services, serve_companion=False)) as client:
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            roots = response.json()["local_source_roots"]
            self.assertEqual(roots[0]["root_id"], "proof")
            self.assertNotIn("host_path", roots[0])
            self.assertNotIn(str(self.root), response.text)

    def test_revision_mismatch_blocks_real_files_work(self) -> None:
        self.write_config()
        deployment = load_local_source_deployment(self.config)
        try:
            runtime = _work_runtime(
                self.base / "work.db", morning=False,
                local_source_registry=deployment.registry,
                local_source_kernel=deployment.kernel,
            )
            revision = deployment.public_state()[0]["configuration_revision"]
            work_id = runtime.accept(
                TaskBrief("Read hello", ("files.read",), "read", "Read hello"),
                "read", inputs={"files.read": {
                    "provider_namespace": "local", "root_id": "proof",
                    "configuration_revision": revision, "relative_path": "hello.txt",
                }},
            )
            deployment.registry.close()
            deployment.registry.register(LocalRootConfig(
                provider_namespace="local", root_id="proof", host_path=str(self.root),
                display_name="Proof files", configuration_revision="changed-revision",
            ))
            result = runtime.run(work_id)
            self.assertEqual(result.status, "waiting")
            receipt = next(
                item for item in runtime.store.list_artifacts(work_id)
                if item.kind == "execution_receipt"
            )
            self.assertEqual(receipt.payload["error_code"], "root_revision_unavailable")
        finally:
            deployment.close()

    def test_live_read_uses_known_bytes_and_controlled_provenance(self) -> None:
        self.write_config()
        deployment = load_local_source_deployment(self.config)
        try:
            runtime = _work_runtime(
                self.base / "live.db", morning=False,
                local_source_registry=deployment.registry,
                local_source_kernel=deployment.kernel,
            )
            revision = deployment.public_state()[0]["configuration_revision"]
            work_id = runtime.accept(
                TaskBrief("Read hello", ("files.read",), "read", "Read hello"),
                "read", inputs={"files.read": {
                    "provider_namespace": "local", "root_id": "proof",
                    "configuration_revision": revision, "relative_path": "hello.txt",
                }},
            )
            self.assertEqual(runtime.run(work_id).status, "completed")
            artifacts = runtime.store.list_artifacts(work_id)
            categories = {item.provenance_category for item in artifacts}
            self.assertTrue({
                "invocation_input", "acquired_observation", "acquired_content",
                "execution_receipt",
            } <= categories)
            content = next(item for item in artifacts if item.provenance_category == "acquired_content")
            self.assertEqual(
                content.payload["source_byte_sha256"],
                hashlib.sha256(b"hello atlas\n").hexdigest(),
            )
            self.assertNotIn(str(self.root), json.dumps([item.payload for item in artifacts]))
        finally:
            deployment.close()


if __name__ == "__main__":
    unittest.main()
