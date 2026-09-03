#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

_LOCATION_PROVIDERS = {"gps", "network", "passive"}
_LOCATION_REQUESTS = {"once", "last"}
_PHONE_NUMBER = re.compile(r"^\+?[0-9]{6,20}$")
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_SMS_CHARS = 5000
_TIMEOUT_SEC = 45


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_number(value: Any) -> str:
    normalized = re.sub(r"[\s()\-]", "", str(value or "").strip())
    if not _PHONE_NUMBER.fullmatch(normalized):
        raise ValueError("recipient number must contain only an optional leading + and 6-20 digits")
    return normalized


def _message(value: Any) -> str:
    text = str(value or "")
    if not text.strip():
        raise ValueError("SMS message must not be empty")
    if "\x00" in text or len(text) > _MAX_SMS_CHARS:
        raise ValueError("SMS message is invalid or too long")
    return text


def _run(args: list[str]) -> str:
    proc = subprocess.run(
        args,
        text=True,
        capture_output=True,
        timeout=_TIMEOUT_SEC,
        check=False,
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if len(stdout.encode()) > _MAX_OUTPUT_BYTES or len(stderr.encode()) > _MAX_OUTPUT_BYTES:
        raise RuntimeError(f"{args[0]} exceeded output limit")
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {(stderr or stdout).strip() or proc.returncode}")
    return stdout.strip()


def _run_json(args: list[str]) -> Any:
    raw = _run(args)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{args[0]} returned invalid JSON") from exc
    if isinstance(value, dict) and value.get("error"):
        raise RuntimeError(str(value["error"]))
    return value


def _location(payload: dict[str, Any]) -> dict[str, Any]:
    provider = str(payload.get("provider") or "gps").strip().casefold()
    request = str(payload.get("request") or "once").strip().casefold()
    if provider not in _LOCATION_PROVIDERS:
        raise ValueError(f"unsupported location provider: {provider}")
    if request not in _LOCATION_REQUESTS:
        raise ValueError(f"unsupported location request: {request}")
    location = _run_json(["termux-location", "-p", provider, "-r", request])
    return {
        "provider_requested": provider,
        "request": request,
        "fresh_fix_requested": request == "once",
        "observed_at": _iso(),
        "location": location,
    }


def _telephony(_payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"observed_at": _iso()}
    successes = 0
    try:
        result["device"] = _run_json(["termux-telephony-deviceinfo"])
        successes += 1
    except Exception as exc:
        result["device_error"] = str(exc)[:1000]
    try:
        result["cells"] = _run_json(["termux-telephony-cellinfo"])
        successes += 1
    except Exception as exc:
        result["cell_error"] = str(exc)[:1000]
    if successes == 0:
        raise RuntimeError("no telephony information could be retrieved")
    return result


def _sms(payload: dict[str, Any]) -> dict[str, Any]:
    number = _normalize_number(payload.get("number"))
    message = _message(payload.get("message"))
    _run(["termux-sms-send", "-n", number, message])
    return {
        "status": "sent",
        "number": number,
        "message_chars": len(message),
        "dispatched_at": _iso(),
    }


def _request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    if len(raw) > _MAX_REQUEST_BYTES:
        raise ValueError("request exceeds bridge limit")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    operation = str(value.get("operation") or "").strip()
    payload = value.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if operation == "location":
        return _location(payload)
    if operation == "telephony":
        return _telephony(payload)
    if operation == "sms":
        return _sms(payload)
    raise ValueError("unsupported phone bridge operation")


def main() -> int:
    try:
        result = _request()
        response = {"ok": True, "result": result}
    except Exception as exc:
        response = {"ok": False, "error": str(exc)[:4000], "completed_at": _iso()}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
