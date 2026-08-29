from __future__ import annotations

from starlette.testclient import TestClient

from atlas_api.app import create_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_COMPANION_PASSWORD", "secret")
    monkeypatch.setenv("ATLAS_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("ATLAS_ENV", "development")
    app = create_app(instance_root=tmp_path / "instance", static_dir=tmp_path / "missing")
    return TestClient(app)


def _login(client: TestClient) -> str:
    response = client.post("/api/auth/login", json={"password": "secret"})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_owner_control_routes_are_authenticated_and_policy_is_live(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/system").status_code == 401
        csrf = _login(client)
        headers = {"X-CSRF-Token": csrf}
        state = client.get("/api/system")
        assert state.status_code == 200
        assert state.json()["version"] == "3.0.0"

        update = client.post(
            "/api/policy",
            json={"scope": "atlas/work", "operation": "create", "decision": "NO"},
            headers=headers,
        )
        assert update.status_code == 200

        denied = client.post(
            "/api/work",
            json={
                "objective": "Search notes",
                "steps": [{"capability_id": "knowledge.search", "input": {"query": "notes"}}],
            },
            headers=headers,
        )
        assert denied.status_code == 409
        assert denied.json()["action"]["status"] == "blocked"
        assert client.get("/api/work").json()["work"] == []

        allow = client.post(
            "/api/policy",
            json={"scope": "atlas/work", "operation": "create", "decision": "YES"},
            headers=headers,
        )
        assert allow.status_code == 200
        created = client.post(
            "/api/work",
            json={
                "objective": "Search notes",
                "steps": [{"capability_id": "knowledge.search", "input": {"query": "notes"}}],
            },
            headers=headers,
        )
        assert created.status_code == 201
        assert created.json()["action"]["status"] == "succeeded"
        assert len(client.get("/api/work").json()["work"]) == 1


def test_csrf_is_required_for_runtime_mutations(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _login(client)
        response = client.post(
            "/api/policy",
            json={"scope": "host/service", "operation": "restart", "decision": "YES"},
        )
        assert response.status_code == 403


def test_conversation_delete_requires_csrf_and_removes_conversation(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        csrf = _login(client)
        created = client.post(
            "/api/chat/conversations",
            json={"title": "Disposable"},
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 201
        cid = created.json()["conversation_id"]
        assert client.delete(f"/api/chat/conversations/{cid}").status_code == 403
        deleted = client.delete(
            f"/api/chat/conversations/{cid}",
            headers={"X-CSRF-Token": csrf},
        )
        assert deleted.status_code == 200
        assert client.get(f"/api/chat/conversations/{cid}").status_code == 404


def test_memory_routes_use_governed_capabilities_and_confirmation(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        csrf = _login(client)
        headers = {"X-CSRF-Token": csrf}
        remembered = client.post(
            "/api/capabilities/memory.remember/invoke",
            json={"input": {"title": "Units", "content": "I prefer metric units."}},
            headers=headers,
        )
        assert remembered.status_code == 200
        action = remembered.json()["action"]
        assert action["status"] == "succeeded"
        item_id = action["result"]["item_id"]

        listed = client.get("/api/memory")
        assert listed.status_code == 200
        assert [row["item_id"] for row in listed.json()["items"]] == [item_id]
        assert client.get(f"/api/memory/{item_id}").json()["item"]["content"] == "I prefer metric units."

        assert client.post(f"/api/memory/{item_id}/purge").status_code == 403
        pending = client.post(f"/api/memory/{item_id}/purge", headers=headers)
        assert pending.status_code == 202
        pending_action = pending.json()["action"]
        assert pending_action["capability_id"] == "memory.purge"
        assert pending_action["status"] == "pending_confirmation"

        confirmed = client.post(
            f"/api/actions/{pending_action['occurrence_id']}/confirm",
            headers=headers,
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["action"]["status"] == "succeeded"
        assert client.get("/api/memory").json()["items"] == []
