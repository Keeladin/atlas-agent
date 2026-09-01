from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any

from atlas_core.actions import ActionResult
from atlas_core.capabilities import (
    CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, ScopeResolution,
)
from atlas_core.sources import validate_component, validate_relative_path
from atlas_core.sources.extractors import DERIVED_RELATIVE_PATH

from .store import LibraryStore


class LibraryRuntime:
    """Deterministic source-library inspection and duplicate consolidation planning."""

    def __init__(self, store: LibraryStore, sources, registry: CapabilityRegistry) -> None:
        self.store = store
        self.sources = sources
        self.registry = registry
        self._register()

    def _register(self) -> None:
        schema = {
            "type": "object",
            "required": ["root_ids"],
            "properties": {
                "root_ids": {"type": "array", "minItems": 1, "maxItems": 32,
                             "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                "max_files": {"type": "integer", "minimum": 1, "maximum": 100000},
            },
            "additionalProperties": False,
        }
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition(
                "library.scan_duplicates",
                "Recursively hash enrolled source roots and identify exact duplicate files without modifying source data.",
                "scan", "internal", schema, source="library",
                tags=("library", "files", "deterministic", "deduplication"),
            ),
            self._scope, self._execute,
            metadata={"scope_hint": "atlas/library"},
        ), replace=True)
        materialize_schema = {
            "type": "object",
            "required": ["scan_id", "destination_root_id"],
            "properties": {
                "scan_id": {"type": "string", "minLength": 1},
                "destination_root_id": {"type": "string", "minLength": 1},
                "destination_relative_path": {"type": "string"},
            },
            "additionalProperties": False,
        }
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition(
                "library.materialize",
                "Copy one canonical file from each exact-content group into a clean enrolled library without overwriting files.",
                "materialize", "external", materialize_schema, source="library",
                tags=("library", "files", "deterministic", "deduplication"),
            ),
            self._materialize_scope, self._materialize_execute,
            metadata={"scope_hint": "atlas/library"},
        ), replace=True)
        review_schema = {
            "type": "object",
            "required": ["root_id", "relative_path", "status"],
            "properties": {
                "root_id": {"type": "string", "minLength": 1},
                "relative_path": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["unreviewed", "reviewed", "approved", "rejected"]},
            },
            "additionalProperties": False,
        }
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition(
                "library.set_review",
                "Set the owner's human review status for a file in the Atlas clean library.",
                "review", "internal", review_schema, source="library",
                tags=("library", "files", "review", "human"),
            ),
            self._review_scope, self._review_execute,
            metadata={"scope_hint": "atlas/library"},
        ), replace=True)

    def _scope(self, payload: dict[str, Any]) -> ScopeResolution:
        roots = [str(item).strip() for item in payload.get("root_ids") or []]
        if not roots or any(not item for item in roots):
            raise ValueError("root_ids must contain enrolled source roots")
        for root_id in roots:
            row = self.sources.store.get(root_id)
            if not row.enabled:
                raise ValueError(f"source root is disabled: {root_id}")
        clean = {"root_ids": roots, "max_files": int(payload.get("max_files") or 10000)}
        return ScopeResolution("atlas/library", clean, f"Scan {len(roots)} source roots for exact duplicates")

    def _execute(self, payload: dict[str, Any]) -> ActionResult:
        scan_id = self.store.create_scan(list(payload["root_ids"]))
        try:
            summary = self.scan(scan_id, list(payload["root_ids"]), max_files=int(payload["max_files"]))
            self.store.finish_scan(scan_id, summary)
            return ActionResult(
                True, {"scan": self.store.get_scan(scan_id)},
                {"ok": True, "operation": "library.scan_duplicates", "scan_id": scan_id, **summary},
            )
        except Exception as exc:
            self.store.fail_scan(scan_id, str(exc))
            return ActionResult(
                False, {"scan_id": scan_id},
                {"ok": False, "operation": "library.scan_duplicates", "scan_id": scan_id},
                error_code="library_scan_failed", error=str(exc),
            )

    def scan(self, scan_id: str, root_ids: list[str], *, max_files: int) -> dict[str, Any]:
        scanned = 0
        total_bytes = 0
        for root_id in root_ids:
            row = self.sources.store.get(root_id)
            revision = self.sources._revision(row)
            queue = ["."]
            while queue:
                directory = queue.pop(0)
                cursor = None
                while True:
                    page = self.sources.kernel.list(
                        row.provider_namespace, row.root_id, directory,
                        page_size=500, cursor=cursor, configuration_revision=revision,
                    )
                    for entry in page.entries:
                        data = entry.to_dict()
                        rel = data["source_ref"]["relative_path"]
                        name = rel.rsplit("/", 1)[-1]
                        if name in {DERIVED_RELATIVE_PATH, row.quarantine_relative_path, ".ssh", ".gnupg", "secrets"}:
                            continue
                        if data["object_type"] == "directory":
                            queue.append(rel)
                            continue
                        if data["object_type"] != "regular_file":
                            continue
                        scanned += 1
                        if scanned > max_files:
                            raise ValueError(f"library scan exceeds max_files ({max_files})")
                        hashed = self.sources.kernel.hash(
                            row.provider_namespace, row.root_id, rel,
                            configuration_revision=revision,
                        ).to_dict()
                        digest = str(hashed.get("byte_sha256") or "")
                        if not digest:
                            raise RuntimeError(f"hash unavailable for {root_id}:{rel}")
                        size = int(hashed.get("byte_size") or 0)
                        total_bytes += size
                        self.store.add_file(
                            scan_id, root_id=root_id, relative_path=rel,
                            byte_size=size, sha256=digest, media_type=data.get("media_type"),
                        )
                    cursor = page.next_cursor
                    if not cursor:
                        break
        rows = self.store.files(scan_id)
        counts: dict[str, int] = {}
        for item in rows:
            counts[item["sha256"]] = counts.get(item["sha256"], 0) + 1
        unique_documents = len(counts)
        duplicate_copies = sum(max(0, count - 1) for count in counts.values())
        duplicate_groups = sum(1 for count in counts.values() if count > 1)
        return {
            "files_scanned": scanned,
            "bytes_scanned": total_bytes,
            "unique_files": unique_documents,
            "duplicate_copies": duplicate_copies,
            "duplicate_groups": duplicate_groups,
        }

    def _materialize_scope(self, payload: dict[str, Any]) -> ScopeResolution:
        scan = self.store.get_scan(str(payload.get("scan_id") or ""))
        if scan["status"] != "completed":
            raise ValueError("library scan must be completed before materialization")
        destination_root_id = str(payload.get("destination_root_id") or "").strip()
        destination = self.sources.store.get(destination_root_id)
        if not destination.enabled:
            raise ValueError("destination root is disabled")
        if destination_root_id in scan["source_roots"]:
            raise ValueError("destination root must be separate from scanned source roots")
        relative = validate_relative_path(str(payload.get("destination_relative_path") or "."))
        clean = {"scan_id": scan["scan_id"], "destination_root_id": destination_root_id,
                 "destination_relative_path": relative}
        scope = f"atlas/library/{scan['scan_id']}/{destination_root_id}"
        return ScopeResolution(scope, clean, f"Materialize {scan['scan_id']} into {destination.display_name}")

    def _materialize_execute(self, payload: dict[str, Any]) -> ActionResult:
        try:
            result = self.materialize(
                payload["scan_id"], payload["destination_root_id"],
                destination_relative_path=payload.get("destination_relative_path") or ".",
            )
            return ActionResult(True, result, {"ok": True, "operation": "library.materialize", **result})
        except ValueError as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "library.materialize"},
                                error_code="library_materialize_invalid", error=str(exc))
        except Exception as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "library.materialize", "retryable": True},
                                error_code="library_materialize_failed", error=str(exc))

    def materialize(self, scan_id: str, destination_root_id: str, *,
                    destination_relative_path: str = ".") -> dict[str, Any]:
        scan = self.store.get_scan(scan_id)
        if scan["status"] != "completed":
            raise ValueError("library scan must be completed before materialization")
        if destination_root_id in scan["source_roots"]:
            raise ValueError("destination root must be separate from scanned source roots")
        destination = self.sources.store.get(destination_root_id)
        if not destination.enabled:
            raise ValueError("destination root is disabled")
        destination_root = self.sources.registry.get(destination.provider_namespace, destination.root_id)
        base = validate_relative_path(destination_relative_path)
        copied = 0; skipped = 0; copied_bytes = 0
        for row in self.store.files(scan_id, canonical_only=True):
            source = self.sources.store.get(row["root_id"])
            source_revision = self.sources._revision(source)
            source_fd, _ref, _info = self.sources.kernel.open_binary(
                source.provider_namespace, source.root_id, row["relative_path"],
                configuration_revision=source_revision,
            )
            target_parts = [part for part in (base, row["root_id"], row["relative_path"]) if part != "."]
            target = validate_relative_path("/".join(target_parts))
            try:
                outcome = self._copy_into_root(
                    source_fd, expected_sha256=row["sha256"], destination_root_fd=destination_root.fd,
                    destination_path=target,
                )
            finally:
                os.close(source_fd)
            if outcome["status"] == "copied":
                copied += 1; copied_bytes += int(outcome["byte_size"])
            else:
                skipped += 1
        return {"scan_id": scan_id, "destination_root_id": destination_root_id,
                "destination_relative_path": base, "copied_files": copied,
                "already_present": skipped, "copied_bytes": copied_bytes,
                "canonical_files": copied + skipped}

    def _copy_into_root(self, source_fd: int, *, expected_sha256: str,
                        destination_root_fd: int, destination_path: str) -> dict[str, Any]:
        parent_path, name = destination_path.rsplit("/", 1) if "/" in destination_path else (".", destination_path)
        validate_component(name)
        parent_fd = self._ensure_directory(destination_root_fd, parent_path)
        tmp = f".atlas-library-{uuid.uuid4().hex}"
        digest = hashlib.sha256(); size = 0
        try:
            try:
                existing = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=parent_fd)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                try:
                    existing_hash = self._hash_fd(existing)
                finally:
                    os.close(existing)
                if existing_hash != expected_sha256:
                    raise ValueError(f"destination collision at {destination_path}")
                return {"status": "already_present", "byte_size": 0, "sha256": existing_hash}
            os.lseek(source_fd, 0, os.SEEK_SET)
            target_fd = os.open(
                tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600, dir_fd=parent_fd,
            )
            try:
                while True:
                    chunk = os.read(source_fd, 256 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk); size += len(chunk)
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(target_fd, view):]
                os.fsync(target_fd)
            finally:
                os.close(target_fd)
            actual = digest.hexdigest()
            if actual != expected_sha256:
                raise ValueError("source changed since duplicate scan; run a new scan")
            self.sources._rename_noreplace(parent_fd, tmp, parent_fd, name)
            os.fsync(parent_fd)
            return {"status": "copied", "byte_size": size, "sha256": actual}
        finally:
            try:
                os.unlink(tmp, dir_fd=parent_fd)
            except OSError:
                pass
            os.close(parent_fd)

    @staticmethod
    def _hash_fd(fd: int) -> str:
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 256 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _ensure_directory(root_fd: int, relative_path: str) -> int:
        clean = validate_relative_path(relative_path)
        current = os.dup(root_fd)
        if clean == ".":
            return current
        try:
            for component in clean.split("/"):
                validate_component(component)
                try:
                    next_fd = os.open(
                        component, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=current,
                    )
                except FileNotFoundError:
                    os.mkdir(component, 0o700, dir_fd=current)
                    next_fd = os.open(
                        component, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=current,
                    )
                os.close(current); current = next_fd
            return current
        except BaseException:
            os.close(current)
            raise

    def _review_scope(self, payload: dict[str, Any]) -> ScopeResolution:
        root_id = str(payload.get("root_id") or "").strip()
        relative_path = validate_relative_path(str(payload.get("relative_path") or ""))
        status = str(payload.get("status") or "").strip()
        if status not in {"unreviewed", "reviewed", "approved", "rejected"}:
            raise ValueError("unsupported library review status")
        row = self.sources.store.get(root_id)
        if row.provider_namespace != "atlas-library":
            raise ValueError("review status is only available for the Atlas clean library")
        observation = self.sources.kernel.stat(
            row.provider_namespace, row.root_id, relative_path,
            configuration_revision=self.sources._revision(row),
        )
        if observation.object_type != "regular_file":
            raise ValueError("review status can only be set on regular files")
        clean = {"root_id": root_id, "relative_path": relative_path, "status": status}
        return ScopeResolution(
            f"atlas/library/review/{root_id}/{relative_path}", clean,
            f"Mark {relative_path} as {status}",
        )

    def _review_execute(self, payload: dict[str, Any]) -> ActionResult:
        try:
            status = None if payload["status"] == "unreviewed" else payload["status"]
            row = self.store.set_review(
                root_id=payload["root_id"], relative_path=payload["relative_path"], status=status,
            )
            return ActionResult(True, row, {"ok": True, "operation": "library.review", **row})
        except Exception as exc:
            return ActionResult(False, receipt={"ok": False, "operation": "library.review"},
                                error_code="library_review_failed", error=str(exc))
