from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
MANIFEST_NAME = ".atlas-rollback-manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _service(command: str, unit: str) -> None:
    proc = subprocess.run(
        ["systemctl", "--user", command, unit], text=True, capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"systemctl {command} failed")
def _service_is_stopped(unit: str) -> bool:
    proc = subprocess.run(
        ["systemctl", "--user", "is-active", unit], text=True, capture_output=True, check=False
    )
    return proc.stdout.strip() in {"inactive", "failed", "unknown"}


def _sqlite_mains(root: Path) -> tuple[Path, ...]:
    rows = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.casefold() in DB_SUFFIXES:
            rows.append(path)
    return tuple(sorted(rows))


def _sqlite_sidecars(root: Path) -> tuple[Path, ...]:
    rows = []
    for path in root.rglob("*"):
        if path.is_file() and path.name.endswith(("-wal", "-shm")):
            rows.append(path)
    return tuple(sorted(rows))


def _schema_fingerprint(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as db:
        user_version = int(db.execute("PRAGMA user_version").fetchone()[0])
        rows = db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        ).fetchall()
    encoded = json.dumps(rows, ensure_ascii=False, default=str, separators=(",", ":"))
    return {"user_version": user_version, "schema_sha256": hashlib.sha256(encoded.encode()).hexdigest()}
def _verify_sqlite(path: Path) -> dict[str, object]:
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        opened = db.execute("SELECT 1").fetchone()[0] == 1
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    if not opened or integrity != "ok":
        raise RuntimeError(f"SQLite rollback verification failed: {path}")
    return {"integrity_check": integrity, **_schema_fingerprint(path)}


def _copy_full_state(instance_root: Path, rollback: Path) -> None:
    """Copy the stopped instance byte-for-byte, preserving symlinks and SQLite sidecars."""
    if rollback.exists():
        raise FileExistsError(rollback)
    shutil.copytree(instance_root, rollback, symlinks=True)


def create_verified_rollback(
    instance_root: Path, *, runtime_revision: str,
    rollback_root: Path | None = None,
) -> Path:
    instance_root = instance_root.resolve()
    if not instance_root.is_dir():
        raise FileNotFoundError(instance_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = rollback_root.resolve() if rollback_root else instance_root.parent
    destination.mkdir(parents=True, exist_ok=True)
    rollback = destination / f"{instance_root.name}.rollback.{stamp}"
    _copy_full_state(instance_root, rollback)

    databases: dict[str, dict[str, object]] = {}
    for source in _sqlite_mains(instance_root):
        rel = source.relative_to(instance_root)
        target = rollback / rel
        databases[rel.as_posix()] = {
            "size": target.stat().st_size,
            "sha256": _sha256(target),
            **_verify_sqlite(target),
        }

    files: dict[str, dict[str, object]] = {}
    for path in sorted(rollback.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        rel = path.relative_to(rollback).as_posix()
        if rel in databases:
            continue
        files[rel] = {"size": path.stat().st_size, "sha256": _sha256(path)}

    manifest = {
        "schema": 3,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(instance_root),
        "runtime_revision": runtime_revision,
        "database_count": len(databases),
        "databases": databases,
        "files": files,
        "verification": "full_state_copy + readonly_open + pragma_integrity_check + sha256",
    }
    (rollback / MANIFEST_NAME).write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return rollback


def reset_sqlite_state(instance_root: Path) -> tuple[str, ...]:
    removed: list[str] = []
    for path in (*_sqlite_mains(instance_root), *_sqlite_sidecars(instance_root)):
        if path.exists():
            removed.append(path.relative_to(instance_root).as_posix())
            path.unlink()
    return tuple(sorted(set(removed)))
def perform_cutover(
    instance_root: Path, *, rollback_root: Path | None = None,
    service_unit: str | None = None, assume_stopped: bool = False,
) -> dict[str, object]:
    if not service_unit and not assume_stopped:
        raise ValueError("provide --service-unit or explicitly use --assume-stopped")
    stopped_by_us = False
    if service_unit:
        _service("stop", service_unit)
        stopped_by_us = True
        if not _service_is_stopped(service_unit):
            raise RuntimeError(f"service did not reach stopped state: {service_unit}")

    from atlas_api.compose import _runtime_revision
    runtime_revision = _runtime_revision()
    rollback: Path | None = None
    success = False
    try:
        rollback = create_verified_rollback(
            instance_root, runtime_revision=runtime_revision, rollback_root=rollback_root
        )
        removed = reset_sqlite_state(instance_root)
        from atlas_api.compose import build_runtime
        runtime = build_runtime(instance_root)
        from atlas_core.obligations import collect_runtime_violations
        violations = collect_runtime_violations(
            runtime.chat_store, runtime.obligation_store, runtime.work_store,
            runtime.actions_store, runtime.evidence,
        )
        if violations:
            raise RuntimeError(
                "fresh cutover schema failed invariants: "
                + "; ".join(f"{x.code}:{x.reference}" for x in violations)
            )
        for path in _sqlite_mains(instance_root):
            _verify_sqlite(path)
        success = True
        return {
            "ok": True,
            "instance_root": str(instance_root.resolve()),
            "rollback": str(rollback),
            "removed_sqlite_files": list(removed),
            "runtime_revision": runtime.runtime_revision,
        }
    finally:
        # A failed cutover must leave the service stopped; do not boot into a half-reset state.
        if success and stopped_by_us:
            _service("start", service_unit)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verified Atlas obligation-ledger development cutover"
    )
    parser.add_argument("instance_root", type=Path)
    parser.add_argument("--rollback-root", type=Path)
    parser.add_argument("--service-unit")
    parser.add_argument(
        "--assume-stopped", action="store_true",
        help="Assert that every Atlas process with access to the instance is stopped",
    )
    parser.add_argument(
        "--confirm-reset", action="store_true",
        help="Required acknowledgement that all SQLite-backed development state will be recreated",
    )
    return parser
def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_reset:
        raise SystemExit("refusing destructive cutover without --confirm-reset")
    result = perform_cutover(
        args.instance_root,
        rollback_root=args.rollback_root,
        service_unit=args.service_unit,
        assume_stopped=args.assume_stopped,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
