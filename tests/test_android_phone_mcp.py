from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from atlas_core.mcp_stdio import StdioMCPClient
from atlas_providers.android_phone_mcp import AndroidPhoneProvider


def _run(coro):
    return asyncio.run(coro)


def test_android_phone_tools_are_narrow_and_annotated(monkeypatch):
    provider = AndroidPhoneProvider()
    monkeypatch.setattr(provider, "_available", lambda _command: True)

    tools = _run(provider.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "phone_location_get", "phone_telephony_inspect", "phone_sms_send",
    }
    assert by_name["phone_location_get"].annotations.read_only_hint is True
    assert "where am I" in by_name["phone_location_get"].description
    assert by_name["phone_telephony_inspect"].annotations.read_only_hint is True
    assert by_name["phone_sms_send"].annotations.read_only_hint is False
    assert by_name["phone_sms_send"].annotations.idempotent_hint is False


def test_location_defaults_to_fresh_gps_once(monkeypatch):
    provider = AndroidPhoneProvider(location_command="termux-location")
    monkeypatch.setattr(provider, "_available", lambda _command: True)
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_json(command: str, *args: str) -> Any:
        calls.append((command, args))
        return {"latitude": -25.7, "longitude": 28.5, "accuracy": 8.0}

    monkeypatch.setattr(provider, "_run_json", fake_json)
    result = _run(provider.call_tool("phone_location_get", {}))

    assert result.is_error is False
    assert calls == [("termux-location", ("-p", "gps", "-r", "once"))]
    assert result.structured_content["fresh_fix_requested"] is True
    assert result.structured_content["location"]["accuracy"] == 8.0


def test_sms_arguments_never_cross_a_shell_boundary(monkeypatch):
    provider = AndroidPhoneProvider(sms_command="termux-sms-send")
    monkeypatch.setattr(provider, "_available", lambda _command: True)
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_text(command: str, *args: str) -> str:
        calls.append((command, args))
        return ""

    monkeypatch.setattr(provider, "_run_text", fake_text)
    message = 'hello "$(touch /tmp/should-not-exist)"; still text'
    result = _run(provider.call_tool("phone_sms_send", {
        "number": "+27 82-123-4567",
        "message": message,
    }))

    assert result.is_error is False
    assert calls == [("termux-sms-send", ("-n", "+27821234567", message))]
    assert result.structured_content["status"] == "sent"
    assert result.structured_content["message_chars"] == len(message)

    invalid = _run(provider.call_tool("phone_sms_send", {
        "number": "+2782;touch/tmp/nope",
        "message": "hello",
    }))
    assert invalid.is_error is True
    assert len(calls) == 1


def test_telephony_can_return_partial_supported_information(monkeypatch):
    provider = AndroidPhoneProvider()
    monkeypatch.setattr(provider, "_available", lambda _command: True)

    async def fake_json(command: str, *args: str) -> Any:
        if command == "termux-telephony-deviceinfo":
            return {"network_operator_name": "Example"}
        raise RuntimeError("cell information unavailable on this Android build")

    monkeypatch.setattr(provider, "_run_json", fake_json)
    result = _run(provider.call_tool("phone_telephony_inspect", {}))

    assert result.is_error is False
    assert result.structured_content["device"]["network_operator_name"] == "Example"
    assert "cell information unavailable" in result.structured_content["cell_error"]


def test_android_phone_server_is_compatible_with_atlas_stdio_client():
    repo = Path(__file__).resolve().parents[1]
    executable = sys.executable
    client = StdioMCPClient(
        executable,
        args=(
            "-m", "atlas_providers.android_phone_mcp",
            "--location-command", executable,
            "--sms-command", executable,
            "--deviceinfo-command", executable,
            "--cellinfo-command", executable,
            "--execution-timeout", "5",
        ),
        cwd=repo,
        timeout_sec=10,
        read_timeout_sec=10,
    )
    try:
        tools = client.list_tools()
    finally:
        client.close()

    names = {tool["name"] for tool in tools}
    assert names == {"phone_location_get", "phone_telephony_inspect", "phone_sms_send"}


def test_ssh_phone_provider_keeps_payload_off_remote_command(monkeypatch, tmp_path):
    from atlas_providers.android_phone_mcp import SSHAndroidPhoneProvider

    calls: list[tuple[tuple[str, ...], bytes]] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self, data):
            calls.append((argv, data))
            return b'{"ok":true,"result":{"status":"sent","number":"+27821234567","message_chars":42}}\n', b""

    async def fake_exec(*args, **_kwargs):
        nonlocal argv
        argv = tuple(str(item) for item in args)
        return FakeProcess()

    argv: tuple[str, ...] = ()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    provider = SSHAndroidPhoneProvider(
        host="100.93.39.106", user="u0_a382", port=8022,
        identity_file=str(tmp_path / "key"), known_hosts_file=str(tmp_path / "known_hosts"),
        remote_bridge="/data/data/com.termux/files/home/.atlas/android_phone_bridge.py",
    )
    message = 'hello "$(touch /tmp/nope)"; still text'
    result = _run(provider.call_tool("phone_sms_send", {
        "number": "+27 82-123-4567", "message": message,
    }))

    assert result.is_error is False
    assert len(calls) == 1
    command, raw = calls[0]
    assert message not in command
    assert command[-1] == "/data/data/com.termux/files/home/.atlas/android_phone_bridge.py"
    request = __import__("json").loads(raw.decode("utf-8"))
    assert request == {
        "operation": "sms",
        "payload": {"number": "+27821234567", "message": message},
    }


def test_remote_phone_mode_requires_complete_ssh_configuration():
    from atlas_providers.android_phone_mcp import main

    try:
        main(["--ssh-host", "100.93.39.106"])
    except SystemExit as exc:
        assert "requires host" in str(exc)
    else:
        raise AssertionError("partial remote phone configuration was accepted")
