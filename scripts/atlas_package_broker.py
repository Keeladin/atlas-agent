#!/usr/bin/python3
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}$")
_APT_GET = "/usr/bin/apt-get"
_MAX_REQUEST = 4096
_MAX_RESPONSE = 32768
_TIMEOUT_SEC = 900


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _package(value: object) -> str:
    package = str(value or "").strip().casefold()
    if not _PACKAGE.fullmatch(package):
        raise ValueError("invalid Debian package name")
    return package


def _command(operation: str, package: str | None = None) -> list[str]:
    if operation == "refresh":
        return [_APT_GET, "update"]
    clean = _package(package)
    if operation == "install":
        return [_APT_GET, "-y", "--no-install-recommends", "install", clean]
    if operation == "remove":
        return [_APT_GET, "-y", "remove", clean]
    raise ValueError("unsupported package operation")


def _execute(operation: str, package: str | None) -> dict[str, object]:
    command = _command(operation, package)
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "DEBIAN_FRONTEND": "noninteractive"}
    proc = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=_TIMEOUT_SEC,
        check=False,
        env=env,
    )
    return {
        "ok": proc.returncode == 0,
        "operation": operation,
        "package": _package(package) if package else None,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-12000:],
        "stderr_tail": proc.stderr[-12000:],
        "completed_at": _iso(),
    }


def _parse_request(raw: bytes) -> tuple[str, str | None]:
    if len(raw) > _MAX_REQUEST:
        raise ValueError("request too large")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    operation = str(value.get("operation") or "")
    if operation == "refresh":
        if set(value) != {"operation"}:
            raise ValueError("refresh accepts no package")
        return operation, None
    if operation not in {"install", "remove"}:
        raise ValueError("unsupported package operation")
    if set(value) != {"operation", "package"}:
        raise ValueError("package request has unexpected fields")
    return operation, _package(value.get("package"))


def _peer_uid(conn: socket.socket) -> int:
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", raw)
    _ = pid, gid
    return uid


def _respond(conn: socket.socket, payload: dict[str, object]) -> None:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(data) > _MAX_RESPONSE:
        data = json.dumps({"ok": False, "error": "response exceeded broker limit"}).encode()
    conn.sendall(data + b"\n")


def _handle(conn: socket.socket, *, allowed_uid: int) -> None:
    try:
        if _peer_uid(conn) != allowed_uid:
            raise PermissionError("peer uid is not authorized")
        chunks = bytearray()
        while len(chunks) <= _MAX_REQUEST:
            part = conn.recv(1024)
            if not part:
                break
            chunks.extend(part)
            if b"\n" in part:
                break
        operation, package = _parse_request(bytes(chunks).split(b"\n", 1)[0])
        _respond(conn, _execute(operation, package))
    except Exception as exc:
        _respond(conn, {"ok": False, "error": str(exc)[:1000], "completed_at": _iso()})


def serve(socket_path: Path, *, allowed_uid: int, socket_gid: int) -> None:
    if os.geteuid() != 0:
        raise PermissionError("broker must run as root")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    os.chown(socket_path.parent, 0, socket_gid)
    os.chmod(socket_path.parent, 0o750)
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chown(socket_path, 0, socket_gid)
    os.chmod(socket_path, 0o660)
    server.listen(4)
    try:
        while True:
            conn, _addr = server.accept()
            with conn:
                _handle(conn, allowed_uid=allowed_uid)
    finally:
        server.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Root package broker for Atlas")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--allowed-uid", type=int, required=True)
    parser.add_argument("--socket-gid", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    serve(
        Path(args.socket),
        allowed_uid=int(args.allowed_uid),
        socket_gid=int(args.socket_gid),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
