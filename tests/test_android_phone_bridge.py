from __future__ import annotations

import importlib.util
from pathlib import Path


def _bridge_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "android_phone_bridge.py"
    spec = importlib.util.spec_from_file_location("atlas_android_phone_bridge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phone_bridge_sms_never_uses_a_shell(monkeypatch):
    bridge = _bridge_module()
    calls: list[list[str]] = []
    monkeypatch.setattr(bridge, "_run", lambda args: calls.append(list(args)) or "")
    message = 'hello "$(touch /tmp/nope)"; still text'

    result = bridge._sms({"number": "+27 82-123-4567", "message": message})

    assert calls == [["termux-sms-send", "-n", "+27821234567", message]]
    assert result["status"] == "sent"


def test_phone_bridge_location_is_fixed_to_termux_location(monkeypatch):
    bridge = _bridge_module()
    calls: list[list[str]] = []

    def fake_json(args):
        calls.append(list(args))
        return {"latitude": -25.0, "longitude": 28.0, "accuracy": 10.0}

    monkeypatch.setattr(bridge, "_run_json", fake_json)
    result = bridge._location({"provider": "gps", "request": "once"})

    assert calls == [["termux-location", "-p", "gps", "-r", "once"]]
    assert result["fresh_fix_requested"] is True


def test_phone_bridge_rejects_unrecognized_operation(monkeypatch):
    bridge = _bridge_module()
    monkeypatch.setattr(bridge, "_request", lambda: (_ for _ in ()).throw(ValueError("unsupported phone bridge operation")))
    assert bridge.main() == 1
