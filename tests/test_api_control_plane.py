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
