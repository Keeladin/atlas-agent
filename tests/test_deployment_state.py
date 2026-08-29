from __future__ import annotations

import os
import stat

from starlette.testclient import TestClient

from atlas_api.app import create_app
from atlas_api.compose import build_runtime


def test_sensitive_host_policy_is_visible_and_owner_can_override(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    secret_scope = "host/filesystem" + str((rt.instance_root / "secrets").resolve())

    generic = rt.policy.resolve(principal_id=owner, scope="host/filesystem/tmp/example", operation="read")
    sensitive = rt.policy.resolve(principal_id=owner, scope=secret_scope + "/master.key", operation="read")
    assert generic.decision == "YES"
    assert sensitive.decision == "NO"

    rt.policy_store.set(principal_id=owner, scope=secret_scope, operation="read", decision="YES")
    overridden = rt.policy.resolve(principal_id=owner, scope=secret_scope + "/master.key", operation="read")
    assert overridden.decision == "YES"


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
