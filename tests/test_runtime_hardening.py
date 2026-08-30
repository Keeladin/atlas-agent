from __future__ import annotations

import os

import pytest
import time
from concurrent.futures import ThreadPoolExecutor

from starlette.requests import Request
from starlette.testclient import TestClient

from atlas_api.app import create_app
from atlas_api.auth import auth_from_env, client_key
from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, CapabilityRuntime, ScopeResolution
from atlas_core.evidence import EvidenceStore
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.providers.runtime import ProviderRuntime
from atlas_core.providers.settings import ProviderSettings


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_COMPANION_PASSWORD", "secret")
    monkeypatch.setenv("ATLAS_SESSION_SECRET", "stable-session-secret")
    monkeypatch.setenv("ATLAS_ENV", "development")
    return TestClient(create_app(instance_root=tmp_path / "instance", static_dir=tmp_path / "missing"))

def test_chat_turn_does_not_block_health_endpoint(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"password": "secret"})
        csrf = login.json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        created = client.post("/api/chat/conversations", json={"title": "Slow"}, headers=headers)
        cid = created.json()["conversation_id"]

        def slow_send(*args, **kwargs):
            time.sleep(0.45)
            return {"turn": {"content": "done"}}

        monkeypatch.setattr(client.app.state.runtime.chat, "send", slow_send)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, f"/api/chat/conversations/{cid}/messages", json={"message": "hello"}, headers=headers)
            time.sleep(0.05)
            started = time.monotonic()
            health = client.get("/api/health")
            elapsed = time.monotonic() - started
            assert health.status_code == 200
            assert elapsed < 0.30
            assert future.result().status_code == 200

class _SecretStub:
    def retrieve(self, ref):
        assert ref == "cred_test"
        return {"api_key": "TOP-SECRET-KEY"}


class _SettingsStub:
    def all(self):
        return ()


def test_provider_runtime_does_not_publish_decrypted_key_to_process_env(monkeypatch):
    runtime = ProviderRuntime(_SettingsStub(), _SecretStub())
    row = ProviderSettings(
        "remote-test", "openai", "gpt-test", None, True, False, 50,
        "cred_test", {}, "now",
    )
    env_name = "ATLAS_PROVIDER_REMOTE_TEST"
    monkeypatch.delenv(env_name, raising=False)
    provider = runtime._build(row)
    assert provider.api_key == "TOP-SECRET-KEY"
    assert env_name not in os.environ

def test_capability_snapshot_reads_policy_once(tmp_path, monkeypatch):
    policy_store = PolicyStore(tmp_path / "identity.db"); policy_store.initialize()
    action_store = ActionStore(tmp_path / "work.db"); action_store.initialize()
    evidence = EvidenceStore(tmp_path / "work.db"); evidence.initialize()
    registry = CapabilityRegistry(); policy = OwnerPolicy(policy_store)
    actions = ActionRuntime(policy=policy, store=action_store, evidence=evidence, executor_resolver=registry.executor)
    capabilities = CapabilityRuntime(registry, actions, policy)
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    for index in range(40):
        registry.register(CapabilityRegistration(
            CapabilityDefinition(f"test.cap{index}", "test", "read", "none", schema),
            lambda payload: ScopeResolution("test/scope", payload, "test"),
            lambda payload: None,
            metadata={"scope_hint": "test/scope"},
        ))
    policy_store.set(principal_id="owner", scope="test/scope", operation="read", decision="YES")
    calls = {"snapshot": 0}
    original_snapshot = policy_store.snapshot

    def counted_snapshot(principal_id):
        calls["snapshot"] += 1
        return original_snapshot(principal_id)

    monkeypatch.setattr(policy_store, "snapshot", counted_snapshot)
    rows = capabilities.snapshot(principal_id="owner")
    assert len(rows) == 40
    assert all(row.policy_decision == "YES" for row in rows)
    assert calls == {"snapshot": 1}

from atlas_core.chat.runtime import ChatRuntime, _bounded


def test_bounded_tool_context_preserves_result_envelopes():
    rows = [
        {"capability_id": "tool.first", "status": "succeeded", "result": {"blob": "A" * 9000}, "instruction_trust": "data_only"},
        {"capability_id": "tool.second", "status": "succeeded", "result": {"blob": "B" * 9000}, "instruction_trust": "data_only"},
    ]
    bounded = _bounded(rows, 5000)
    assert isinstance(bounded, list)
    kept = [row for row in bounded if isinstance(row, dict) and row.get("capability_id")]
    assert kept
    assert kept[-1]["capability_id"] == "tool.second"
    assert kept[-1]["status"] == "succeeded"
    assert kept[-1]["instruction_trust"] == "data_only"
    assert "truncated_content" in kept[-1]


def test_unmatched_capability_search_falls_back_only_to_core_signposts():
    registry = CapabilityRegistry()
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register(CapabilityRegistration(
        CapabilityDefinition("calendar.alpha", "calendar alpha", "read", "none", schema),
        lambda payload: ScopeResolution("calendar", payload, "calendar"), lambda payload: None,
    ))
    registry.register(CapabilityRegistration(
        CapabilityDefinition("memory.search", "Search durable owner memory", "search", "none", schema),
        lambda payload: ScopeResolution("atlas/memory", payload, "memory"), lambda payload: None,
    ))
    runtime = ChatRuntime(None, None, registry, None, None, None, None)
    matches = runtime.search_capabilities("zzzxxyy unmatched nonsense", limit=36)
    assert [row["id"] for row in matches] == ["memory.search"]


def test_auth_requires_stable_session_secret(monkeypatch):
    monkeypatch.setenv("ATLAS_COMPANION_PASSWORD", "secret")
    monkeypatch.delenv("ATLAS_SESSION_SECRET", raising=False)
    monkeypatch.delenv("ATLAS_API_SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="must be set so sessions survive restart"):
        auth_from_env(env_file=None)


def test_forwarded_for_is_trusted_only_from_loopback_proxy():
    loopback = Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", b"203.0.113.7")], "client": ("127.0.0.1", 1234), "server": ("test", 80), "scheme": "http"})
    remote = Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", b"203.0.113.7")], "client": ("198.51.100.9", 1234), "server": ("test", 80), "scheme": "http"})
    assert client_key(loopback) == "203.0.113.7"
    assert client_key(remote) == "198.51.100.9"
