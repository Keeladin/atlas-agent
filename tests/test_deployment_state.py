from __future__ import annotations

import os
import stat

from starlette.testclient import TestClient

from atlas_api.app import create_app
from atlas_api.compose import build_runtime
from atlas_core.provenance import InvocationProvenance


def test_sensitive_host_paths_are_registry_boundaries_not_policy_rows(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    secret = rt.instance_root / "secrets" / "master.key"
    secret.parent.mkdir(parents=True, exist_ok=True); secret.write_text("secret")

    assert rt.policy.resolve(principal_id=owner, scope=f"host/filesystem{secret}", operation="read").decision == "YES"
    # Even an explicit YES cannot widen a capability's hard path boundary.
    rt.policy_store.set(principal_id=owner, scope=f"host/filesystem{secret.parent}", operation="read", decision="YES")
    try:
        rt.capabilities.invoke("host.filesystem.read", {"path": str(secret)}, provenance=InvocationProvenance(owner, "human", "control"))
    except ValueError as exc:
        assert "capability boundary" in str(exc)
    else:
        raise AssertionError("protected path escaped the host filesystem capability contract")


def test_credential_store_is_group_private(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    root_mode = stat.S_IMODE(os.stat(rt.credentials.root).st_mode)
    key_mode = stat.S_IMODE(os.stat(rt.credentials.key_path).st_mode)
    db_mode = stat.S_IMODE(os.stat(rt.credentials.db_path).st_mode)
    assert root_mode == 0o770
    assert key_mode == 0o660
    assert db_mode == 0o660


def test_auth_is_loaded_from_explicit_instance_root(tmp_path, monkeypatch):
    for key in (
        "ATLAS_COMPANION_PASSWORD", "ATLAS_API_PASSWORD",
        "ATLAS_SESSION_SECRET", "ATLAS_API_SESSION_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    instance = tmp_path / "state"
    instance.mkdir()
    (instance / "companion-auth.env").write_text(
        "ATLAS_COMPANION_PASSWORD=external-state-password\n"
        "ATLAS_SESSION_SECRET=external-state-secret\n"
        "ATLAS_SECURE_COOKIES=0\n",
        encoding="utf-8",
    )
    app = create_app(instance_root=instance, static_dir=tmp_path / "missing")
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"password": "external-state-password"})
        assert response.status_code == 200
        assert response.json()["authenticated"] is True
