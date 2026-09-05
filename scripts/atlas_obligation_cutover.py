from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Iterable

DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}

def _service(command: str, unit: str) -> None:
    proc = subprocess.run(["systemctl", "--user", command, unit], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"systemctl {command} failed")

def _service_is_stopped(unit: str) -> bool:
    proc = subprocess.run(["systemctl", "--user", "is-active", unit], text=True, capture_output=True, check=False)
    return proc.stdout.strip() in {"inactive", "failed", "unknown"}

def _sqlite_state(root: Path) -> tuple[Path, ...]:
    rows = []
    for path in root.rglob("*"):
        if path.is_file() and (path.suffix.casefold() in DB_SUFFIXES or path.name.endswith(("-wal", "-shm"))):
            rows.append(path)
    return tuple(sorted(rows))

def _verify_sqlite(path: Path) -> None:
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"SQLite verification failed: {path}")

def reset_sqlite_state(instance_root: Path) -> tuple[str, ...]:
    removed = []
    for path in _sqlite_state(instance_root):
        removed.append(path.relative_to(instance_root).as_posix())
        path.unlink()
    return tuple(removed)

def perform_cutover(instance_root: Path, *, service_unit: str | None = None, assume_stopped: bool = False) -> dict[str, object]:
    instance_root = instance_root.resolve()
    if not instance_root.is_dir():
        raise FileNotFoundError(instance_root)
    if not service_unit and not assume_stopped:
        raise ValueError("provide --service-unit or explicitly use --assume-stopped")
    stopped_by_us = False
    if service_unit:
        _service("stop", service_unit); stopped_by_us = True
        if not _service_is_stopped(service_unit):
            raise RuntimeError(f"service did not reach stopped state: {service_unit}")
    success = False
    try:
        removed = reset_sqlite_state(instance_root)
        from atlas_api.compose import build_runtime
        runtime = build_runtime(instance_root)
        from atlas_core.obligations import collect_runtime_violations
        violations = collect_runtime_violations(runtime.chat_store, runtime.obligation_store, runtime.work_store, runtime.actions_store, runtime.evidence)
        if violations:
            raise RuntimeError("fresh cutover schema failed invariants: " + "; ".join(f"{x.code}:{x.reference}" for x in violations))
        for path in _sqlite_state(instance_root):
            if path.suffix.casefold() in DB_SUFFIXES:
                _verify_sqlite(path)
        success = True
        return {"ok": True, "instance_root": str(instance_root), "removed_sqlite_files": list(removed), "runtime_revision": runtime.runtime_revision}
    finally:
        if success and stopped_by_us:
            _service("start", service_unit)

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Destructive Atlas obligation-ledger development cutover")
    parser.add_argument("instance_root", type=Path)
    parser.add_argument("--service-unit")
    parser.add_argument("--assume-stopped", action="store_true", help="Assert every Atlas process with access to the instance is stopped")
    parser.add_argument("--confirm-reset", action="store_true", help="Required acknowledgement that all SQLite-backed development state will be destroyed and recreated")
    return parser

def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_reset:
        raise SystemExit("refusing destructive cutover without --confirm-reset")
    print(json.dumps(perform_cutover(args.instance_root, service_unit=args.service_unit, assume_stopped=args.assume_stopped), sort_keys=True, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
