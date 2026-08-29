from __future__ import annotations

import subprocess

import atlas_core.host as host_module
from atlas_api.compose import build_runtime
from atlas_core.provenance import InvocationProvenance


def _completed(args, stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)


def test_host_service_uses_user_manager_and_runtime_confirm(tmp_path, monkeypatch):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    calls: list[list[str]] = []

    def fake_run(args, timeout=20):
        calls.append(list(args))
        if "show" in args:
            return _completed(args, "Id=demo.service\nActiveState=active\n")
        return _completed(args)

    monkeypatch.setattr(host_module, "_run", fake_run)
    provenance = InvocationProvenance(owner, "human", "chat")

    status = rt.capabilities.invoke(
        "host.service.status", {"unit": "demo.service"}, provenance=provenance,
    )
    assert status.status == "succeeded"
    assert calls[-1][:3] == ["systemctl", "--user", "show"]

    restart = rt.capabilities.invoke(
        "host.service.restart", {"unit": "demo.service"}, provenance=provenance,
    )
    assert restart.status == "pending_confirmation"
    assert not any("restart" in call for call in calls)

    confirmed = rt.actions.confirm(restart.occurrence_id, principal_id=owner)
    assert confirmed.status == "succeeded"
    assert calls[-1] == ["systemctl", "--user", "restart", "demo.service", "--no-block"]

    logs = rt.capabilities.invoke(
        "host.service.logs", {"unit": "demo.service", "lines": 20}, provenance=provenance,
    )
    assert logs.status == "succeeded"
    assert calls[-1][:2] == ["journalctl", "--user-unit"]


def test_exact_service_name_is_required(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    provenance = InvocationProvenance(owner, "human", "chat")
    try:
        rt.capabilities.invoke(
            "host.service.restart", {"unit": "../atlas-api.service"}, provenance=provenance,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("service path smuggling was accepted")
