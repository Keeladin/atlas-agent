from __future__ import annotations

import subprocess

import pytest

import atlas_core.host as host_module
from atlas_api.compose import build_runtime
from atlas_core.provenance import InvocationProvenance
from scripts import atlas_package_broker as broker


def _completed(args, stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)


def test_package_inspect_is_read_only_and_reports_versions(tmp_path, monkeypatch):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    calls: list[list[str]] = []

    def fake_run(args, timeout=20):
        calls.append(args)
        if args[0].endswith("dpkg-query"):
            return _completed(args, "install ok installed\n1.2.3\n")
        if args[0].endswith("apt-cache"):
            return _completed(args, "tailscale:\n  Installed: 1.2.3\n  Candidate: 1.4.0\n")
        raise AssertionError(args)

    monkeypatch.setattr(host_module, "_run", fake_run)
    result = rt.capabilities.invoke(
        "host.package.inspect", {"package": "TAILSCALE"},
        provenance=InvocationProvenance(owner, "human", "chat"),
    )
    assert result.status == "succeeded"
    assert result.result == {
        "package": "tailscale",
        "installed": True,
        "installed_version": "1.2.3",
        "candidate_version": "1.4.0",
    }
    assert calls == [
        ["/usr/bin/dpkg-query", "-W", "-f=${Status}\n${Version}\n", "tailscale"],
        ["/usr/bin/apt-cache", "policy", "tailscale"],
    ]


def test_package_install_uses_principal_yes_then_narrow_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(host_module, "_trusted_package_broker", lambda: (True, "available"))
    calls: list[tuple[str, str | None]] = []

    def fake_broker_call(op: str, package: str | None):
        calls.append((op, package))
        return {"ok": True, "operation": op, "package": package}

    monkeypatch.setattr(host_module.HostRuntime, "_package_broker_call", staticmethod(fake_broker_call))
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    completed = rt.capabilities.invoke(
        "host.package.install", {"package": "tailscale"},
        provenance=InvocationProvenance(owner, "human", "chat"),
    )
    assert completed.status == "succeeded"
    assert calls == [("install", "tailscale")]

    rt.policy_store.set(principal_id=owner, scope="host/package/tailscale", operation="install", decision="NO")
    blocked = rt.capabilities.invoke("host.package.install", {"package": "tailscale"}, provenance=InvocationProvenance(owner, "human", "chat"))
    assert blocked.status == "blocked"
    assert calls == [("install", "tailscale")]


def test_package_scope_rejects_command_or_path_smuggling(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    provenance = InvocationProvenance(owner, "human", "chat")
    for value in ("tailscale;id", "-oDpkg::Pre-Invoke=id", "./tailscale.deb", "foo/bar"):
        with pytest.raises(ValueError):
            rt.capabilities.invoke(
                "host.package.inspect", {"package": value}, provenance=provenance,
            )


def test_package_broker_constructs_only_fixed_apt_commands():
    assert broker._command("install", "tailscale") == [
        "/usr/bin/apt-get", "-y", "--no-install-recommends", "install", "tailscale",
    ]
    assert broker._command("remove", "tailscale") == [
        "/usr/bin/apt-get", "-y", "remove", "tailscale",
    ]
    assert broker._command("refresh") == ["/usr/bin/apt-get", "update"]
    with pytest.raises(ValueError):
        broker._command("install", "../../tmp/payload.deb")
    assert broker._parse_request(b'{"operation":"install","package":"tailscale"}') == ("install", "tailscale")
    with pytest.raises(ValueError):
        broker._parse_request(b'{"operation":"install","package":"tailscale;id"}')


def test_package_policy_is_one_coarse_principal_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(host_module, "_trusted_package_broker", lambda: (True, "available"))
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    assert rt.policy.resolve(
        principal_id=owner, scope="host/package/tailscale", operation="inspect",
    ).decision == "YES"
    assert rt.policy.resolve(
        principal_id=owner, scope="host/package/tailscale", operation="install",
    ).decision == "YES"
    assert rt.policy.resolve(
        principal_id=owner, scope="host/package/tailscale", operation="remove",
    ).decision == "YES"
    assert rt.policy.resolve(
        principal_id=owner, scope="host/package/index", operation="refresh",
    ).decision == "YES"
    assert rt.chat.search_capabilities("install tailscale", limit=5)[0]["id"] == "host.package.install"
