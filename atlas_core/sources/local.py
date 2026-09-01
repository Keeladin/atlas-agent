from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import stat as stat_module
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock
from typing import Any, Callable

from .contracts import ObjectType, SourceObservation, SourceRef
from .errors import LocalSourceError, LocalSourceErrorCode


FILESYSTEM_POLICY_VERSION = "local-files-v1"
DEFAULT_LIST_PAGE_SIZE = 100
MAX_LIST_PAGE_SIZE = 500
MAX_READ_BYTES = 4 * 1024 * 1024
MAX_INSPECTION_BYTES = 2 * 1024 * 1024
MAX_EXTRACTION_BYTES = 64 * 1024 * 1024
MAX_HASH_BYTES = 1024 * 1024 * 1024
LIST_STAT_TIMEOUT_SECONDS = 10.0
READ_HASH_TIMEOUT_SECONDS = 60.0
MAX_RESULT_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 512 * 1024
MAX_PATH_BYTES = 4096
STREAM_BUFFER_BYTES = 64 * 1024

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_OPENAT2_SYSCALL = 437
_RESOLVE_NO_XDEV = 0x01
_RESOLVE_NO_MAGICLINKS = 0x02
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_OPENAT2_RESOLVE = (
    _RESOLVE_BENEATH | _RESOLVE_NO_MAGICLINKS | _RESOLVE_NO_SYMLINKS | _RESOLVE_NO_XDEV
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


@dataclass(frozen=True)
class LocalRootConfig:
    root_id: str
    provider_namespace: str
    host_path: str
    display_name: str | None = None
    read_allowed: bool = True
    mutation_allowed: bool = False
    allow_cross_mounts: bool = False
    configuration_revision: str = "1"
    quarantine_relative_path: str | None = None
    derived_relative_path: str | None = None


@dataclass
class CancellationToken:
    _event: Event = field(default_factory=Event)

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class SourceReadResult:
    observation: SourceObservation
    text: str
    encoding: str
    bom: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation": self.observation.to_dict(),
            "content": {"text": self.text, "encoding": self.encoding, "bom": self.bom},
        }


@dataclass(frozen=True)
class SourceListResult:
    observation: SourceObservation
    entries: tuple[SourceObservation, ...]
    next_cursor: str | None
    entry_errors: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation": self.observation.to_dict(),
            "entries": [item.to_dict() for item in self.entries],
            "next_cursor": self.next_cursor,
            "entry_errors": list(self.entry_errors),
        }


@dataclass
class _RegisteredRoot:
    config: LocalRootConfig
    canonical_host_path: str
    fd: int
    device: int
    inode: int
    quarantine_fd: int | None = None
    derived_fd: int | None = None


class _OpenHow(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]


class LocalRootRegistry:
    """Deployment-owned roots with retained canonical directory descriptors."""

    def __init__(self) -> None:
        if platform.system() != "Linux":
            raise LocalSourceError("unsupported_platform", "Local source acquisition requires Linux.")
        self._roots: dict[tuple[str, str], _RegisteredRoot] = {}
        self._lock = RLock()

    def register(self, config: LocalRootConfig) -> None:
        if not _IDENTIFIER.fullmatch(config.root_id) or not _IDENTIFIER.fullmatch(config.provider_namespace) or not config.configuration_revision:
            raise LocalSourceError("invalid_path", "Root identity and revision must not be empty.", root_id=config.root_id or None)
        if config.display_name is not None and (
            not config.display_name
            or "/" in config.display_name
            or "\\" in config.display_name
            or any(ord(char) < 32 or ord(char) == 127 for char in config.display_name)
        ):
            raise LocalSourceError("invalid_path", "Root display name is not safe for display.", root_id=config.root_id)
        if config.allow_cross_mounts:
            raise LocalSourceError("operation_not_allowed", "Cross-mount roots are not supported by local-files-v1.", root_id=config.root_id)
        host_path = config.host_path
        if not os.path.isabs(host_path):
            raise LocalSourceError("invalid_path", "Configured root must be an absolute directory.", root_id=config.root_id)
        try:
            before = os.lstat(host_path)
        except FileNotFoundError as exc:
            raise LocalSourceError("missing", "Configured root does not exist.", root_id=config.root_id) from exc
        except PermissionError as exc:
            raise LocalSourceError("permission_denied", "Configured root is not accessible.", root_id=config.root_id) from exc
        if stat_module.S_ISLNK(before.st_mode):
            raise LocalSourceError("symlink_rejected", "Configured root must not be a symlink.", root_id=config.root_id)
        if not stat_module.S_ISDIR(before.st_mode):
            raise LocalSourceError("wrong_type", "Configured root must be a directory.", root_id=config.root_id)
        canonical = os.path.realpath(host_path)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            fd = os.open(canonical, flags)
        except OSError as exc:
            raise _os_error(exc, config.root_id, None)
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            os.close(fd)
            raise LocalSourceError("internal_invariant", "Configured root changed during registration.", root_id=config.root_id)
        quarantine_fd: int | None = None
        quarantine_path = config.quarantine_relative_path
        if quarantine_path is not None:
            if not config.mutation_allowed:
                os.close(fd)
                raise LocalSourceError(
                    "operation_not_allowed",
                    "Managed quarantine requires mutation access for the root.",
                    root_id=config.root_id,
                )
            try:
                quarantine_path = validate_relative_path(quarantine_path)
                if quarantine_path == ".":
                    raise LocalSourceError(
                        "invalid_path", "Managed quarantine must be below the configured root.",
                        root_id=config.root_id,
                    )
                quarantine_fd = _open_retained_directory(
                    fd, quarantine_path, opened.st_dev, config.root_id
                )
            except BaseException:
                os.close(fd)
                raise
        derived_fd: int | None = None
        derived_path = config.derived_relative_path
        if derived_path is not None:
            if not config.mutation_allowed:
                os.close(fd)
                if quarantine_fd is not None:
                    os.close(quarantine_fd)
                raise LocalSourceError(
                    "operation_not_allowed",
                    "Managed derived area requires mutation access for the root.",
                    root_id=config.root_id,
                )
            try:
                derived_path = validate_relative_path(derived_path)
                if derived_path == ".":
                    raise LocalSourceError(
                        "invalid_path", "Managed derived area must be below the configured root.",
                        root_id=config.root_id,
                    )
                derived_fd = _open_retained_directory(
                    fd, derived_path, opened.st_dev, config.root_id
                )
            except BaseException:
                os.close(fd)
                if quarantine_fd is not None:
                    os.close(quarantine_fd)
                raise
        normalized = LocalRootConfig(
            root_id=config.root_id,
            provider_namespace=config.provider_namespace,
            host_path=canonical,
            display_name=config.display_name,
            read_allowed=config.read_allowed,
            mutation_allowed=config.mutation_allowed,
            allow_cross_mounts=False,
            configuration_revision=config.configuration_revision,
            quarantine_relative_path=quarantine_path,
            derived_relative_path=derived_path,
        )
        registered = _RegisteredRoot(
            normalized, canonical, fd, opened.st_dev, opened.st_ino, quarantine_fd, derived_fd
        )
        key = (config.provider_namespace, config.root_id)
        with self._lock:
            existing = self._roots.get(key)
            if existing is not None:
                same_quarantine = (
                    existing.quarantine_fd is None and registered.quarantine_fd is None
                ) or (
                    existing.quarantine_fd is not None
                    and registered.quarantine_fd is not None
                    and (
                        os.fstat(existing.quarantine_fd).st_dev,
                        os.fstat(existing.quarantine_fd).st_ino,
                    ) == (
                        os.fstat(registered.quarantine_fd).st_dev,
                        os.fstat(registered.quarantine_fd).st_ino,
                    )
                )
                same = (
                    existing.device == registered.device
                    and existing.inode == registered.inode
                    and existing.config == registered.config
                    and same_quarantine
                )
                os.close(fd)
                if quarantine_fd is not None:
                    os.close(quarantine_fd)
                if derived_fd is not None:
                    os.close(derived_fd)
                if same:
                    return
                raise LocalSourceError("root_revision_unavailable", "Root identity is already registered for a different target or policy.", root_id=config.root_id)
            self._roots[key] = registered

    def get(self, provider_namespace: str, root_id: str, *, configuration_revision: str | None = None) -> _RegisteredRoot:
        with self._lock:
            root = self._roots.get((provider_namespace, root_id))
        if root is None:
            raise LocalSourceError("root_unknown", "Configured root is unavailable.", root_id=root_id)
        if configuration_revision is not None and root.config.configuration_revision != configuration_revision:
            raise LocalSourceError("root_revision_unavailable", "Requested root configuration revision is unavailable.", root_id=root_id)
        return root

    def execution_policies(self) -> tuple[dict[str, Any], ...]:
        """Safe root identities/policies for deployment-time capability pinning."""

        with self._lock:
            roots = tuple(self._roots.values())
        return tuple(
            {
                "provider_namespace": root.config.provider_namespace,
                "root_id": root.config.root_id,
                "configuration_revision": root.config.configuration_revision,
                "read_allowed": root.config.read_allowed,
                "mutation_allowed": root.config.mutation_allowed,
                "allow_cross_mounts": root.config.allow_cross_mounts,
                "display_name": root.config.display_name,
                "availability": "available",
                "quarantine_available": root.quarantine_fd is not None,
            }
            for root in sorted(
                roots,
                key=lambda item: (item.config.provider_namespace, item.config.root_id),
            )
        )

    def close(self) -> None:
        with self._lock:
            roots, self._roots = self._roots, {}
        for root in roots.values():
            if root.quarantine_fd is not None:
                os.close(root.quarantine_fd)
            if root.derived_fd is not None:
                os.close(root.derived_fd)
            os.close(root.fd)

    def __enter__(self) -> LocalRootRegistry:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class LocalSourceKernel:
    def __init__(
        self,
        registry: LocalRootRegistry,
        *,
        cursor_secret: bytes | None = None,
        clock: Callable[[], float] = time.monotonic,
        stream_hook: Callable[[int], None] | None = None,
    ) -> None:
        self.registry = registry
        self._cursor_secret = cursor_secret or secrets.token_bytes(32)
        self._clock = clock
        self._stream_hook = stream_hook
        self._libc = ctypes.CDLL(None, use_errno=True)
        self._openat2_available = True

    def source_ref(self, provider_namespace: str, root_id: str, relative_path: str) -> SourceRef:
        path = validate_relative_path(relative_path)
        root = self.registry.get(provider_namespace, root_id)
        display_root = root.config.display_name or root_id
        return SourceRef(
            kind="local_file",
            provider_namespace=provider_namespace,
            source_id=f"{root_id}:{path}",
            root_id=root_id,
            relative_path=path,
            display_locator=f"{display_root}/{path}" if path != "." else display_root,
        )

    def open_binary(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        configuration_revision: str | None = None, cancellation: CancellationToken | None = None,
        timeout_seconds: float = READ_HASH_TIMEOUT_SECONDS,
    ) -> tuple[int, SourceRef, os.stat_result]:
        """Open one governed regular file for bounded caller-controlled streaming.

        The returned descriptor is already containment-checked and no-follow opened.
        The caller owns and must close it.
        """
        deadline = self._deadline(timeout_seconds, READ_HASH_TIMEOUT_SECONDS)
        root, ref = self._prepare(
            provider_namespace, root_id, relative_path, configuration_revision, cancellation, deadline
        )
        fd = self._open_content(root, ref.relative_path)
        try:
            info = os.fstat(fd)
            self._require_regular(info, root_id, ref.relative_path)
            self._check(cancellation, deadline, root_id, ref.relative_path)
            return fd, ref, info
        except BaseException:
            os.close(fd)
            raise

    def stat(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        configuration_revision: str | None = None, cancellation: CancellationToken | None = None,
        timeout_seconds: float = LIST_STAT_TIMEOUT_SECONDS,
    ) -> SourceObservation:
        deadline = self._deadline(timeout_seconds, LIST_STAT_TIMEOUT_SECONDS)
        root, ref = self._prepare(provider_namespace, root_id, relative_path, configuration_revision, cancellation, deadline)
        info = self._lstat(root, ref.relative_path)
        self._check(cancellation, deadline, root_id, ref.relative_path)
        metadata = _stat_metadata(info)
        observation = SourceObservation.create(
            source_ref=ref, observed_at=_utc_now(), observation_kind="metadata",
            object_type=_object_type(info.st_mode), consistency="metadata_only",
            completeness="metadata_only", byte_size=info.st_size if stat_module.S_ISREG(info.st_mode) else None,
            metadata=metadata, acquisition=self._acquisition(root, "stat"),
        )
        self._enforce_metadata(observation.metadata, root_id, ref.relative_path)
        self._enforce_result(observation.to_dict(), root_id, ref.relative_path)
        return observation

    def observe_absence(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        configuration_revision: str | None = None,
        cancellation: CancellationToken | None = None,
        timeout_seconds: float = LIST_STAT_TIMEOUT_SECONDS,
    ) -> SourceObservation:
        """Record a stable controlled negative observation for a missing pathname."""

        deadline = self._deadline(timeout_seconds, LIST_STAT_TIMEOUT_SECONDS)
        root, ref = self._prepare(
            provider_namespace, root_id, relative_path, configuration_revision,
            cancellation, deadline,
        )
        if ref.relative_path == ".":
            raise LocalSourceError(
                "wrong_type", "Configured root is present.", root_id=root_id,
                relative_path=ref.relative_path,
            )
        parent, name = (
            ref.relative_path.rsplit("/", 1)
            if "/" in ref.relative_path else (".", ref.relative_path)
        )
        parent_fd = self._open_directory(root, parent)
        try:
            pre = os.fstat(parent_fd)
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise LocalSourceError(
                    "wrong_type", "Source exists; absence precondition is not satisfied.",
                    root_id=root_id, relative_path=ref.relative_path,
                )
            self._check(cancellation, deadline, root_id, ref.relative_path)
            post = os.fstat(parent_fd)
        except OSError as exc:
            raise _os_error(exc, root_id, ref.relative_path)
        finally:
            os.close(parent_fd)
        if _has_drift(pre, post):
            raise LocalSourceError(
                "drifted", "Parent directory changed during absence observation.",
                root_id=root_id, relative_path=ref.relative_path,
            )
        observation = SourceObservation.create(
            source_ref=ref,
            observed_at=_utc_now(),
            observation_kind="absence",
            object_type="missing",
            consistency="stable",
            completeness="metadata_only",
            metadata={"parent_pre": _stat_metadata(pre), "parent_post": _stat_metadata(post)},
            acquisition=self._acquisition(root, "absence"),
        )
        self._enforce_metadata(observation.metadata, root_id, ref.relative_path)
        self._enforce_result(observation.to_dict(), root_id, ref.relative_path)
        return observation

    def hash(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        configuration_revision: str | None = None, cancellation: CancellationToken | None = None,
        timeout_seconds: float = READ_HASH_TIMEOUT_SECONDS,
    ) -> SourceObservation:
        deadline = self._deadline(timeout_seconds, READ_HASH_TIMEOUT_SECONDS)
        root, ref = self._prepare(provider_namespace, root_id, relative_path, configuration_revision, cancellation, deadline)
        fd = self._open_content(root, ref.relative_path)
        try:
            pre = os.fstat(fd)
            self._require_regular(pre, root_id, ref.relative_path)
            if pre.st_size > MAX_HASH_BYTES:
                raise LocalSourceError("too_large", "Source exceeds the hash byte limit.", root_id=root_id, relative_path=ref.relative_path)
            digest = hashlib.sha256()
            total = 0
            while True:
                self._check(cancellation, deadline, root_id, ref.relative_path)
                chunk = os.read(fd, STREAM_BUFFER_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_HASH_BYTES:
                    raise LocalSourceError("too_large", "Source exceeds the hash byte limit.", root_id=root_id, relative_path=ref.relative_path)
                digest.update(chunk)
                if self._stream_hook:
                    self._stream_hook(total)
            post = os.fstat(fd)
        except OSError as exc:
            raise _os_error(exc, root_id, ref.relative_path)
        finally:
            os.close(fd)
        return self._stream_observation(root, ref, "hash", pre, post, total, digest.hexdigest())

    def probe(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        max_bytes: int = MAX_INSPECTION_BYTES, configuration_revision: str | None = None,
        cancellation: CancellationToken | None = None, timeout_seconds: float = READ_HASH_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Read a bounded byte probe for deterministic structural inspection.

        The probe uses the same retained-root/openat containment as ordinary reads.
        Raw bytes stay inside the runtime; callers should expose only bounded
        structural observations derived from them.
        """
        limit = max(1, min(int(max_bytes), MAX_READ_BYTES))
        deadline = self._deadline(timeout_seconds, READ_HASH_TIMEOUT_SECONDS)
        root, ref = self._prepare(provider_namespace, root_id, relative_path, configuration_revision, cancellation, deadline)
        fd = self._open_content(root, ref.relative_path)
        chunks: list[bytes] = []
        try:
            pre = os.fstat(fd)
            self._require_regular(pre, root_id, ref.relative_path)
            remaining = min(pre.st_size, limit)
            digest = hashlib.sha256() if pre.st_size <= limit else None
            total = 0
            while remaining > 0:
                self._check(cancellation, deadline, root_id, ref.relative_path)
                chunk = os.read(fd, min(STREAM_BUFFER_BYTES, remaining))
                if not chunk:
                    break
                chunks.append(chunk); total += len(chunk); remaining -= len(chunk)
                if digest is not None: digest.update(chunk)
            post = os.fstat(fd)
        except OSError as exc:
            raise _os_error(exc, root_id, ref.relative_path)
        finally:
            os.close(fd)
        if _has_drift(pre, post):
            raise LocalSourceError("drifted", "Source changed during inspection probe.", root_id=root_id, relative_path=ref.relative_path)
        complete = total == pre.st_size
        observation = SourceObservation.create(
            source_ref=ref, observed_at=_utc_now(), observation_kind="content", object_type="regular_file",
            consistency="stable", completeness="complete" if complete else "bounded", byte_size=pre.st_size,
            byte_sha256=digest.hexdigest() if digest is not None and complete else None,
            metadata={"pre": _stat_metadata(pre), "post": _stat_metadata(post), "probe_bytes": total},
            acquisition=self._acquisition(root, "inspect"),
        )
        return {"observation": observation.to_dict(), "raw": b"".join(chunks), "complete": complete}

    def read(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        configuration_revision: str | None = None, cancellation: CancellationToken | None = None,
        timeout_seconds: float = READ_HASH_TIMEOUT_SECONDS,
    ) -> SourceReadResult:
        deadline = self._deadline(timeout_seconds, READ_HASH_TIMEOUT_SECONDS)
        root, ref = self._prepare(provider_namespace, root_id, relative_path, configuration_revision, cancellation, deadline)
        fd = self._open_content(root, ref.relative_path)
        chunks: list[bytes] = []
        try:
            pre = os.fstat(fd)
            self._require_regular(pre, root_id, ref.relative_path)
            if pre.st_size > MAX_READ_BYTES:
                raise LocalSourceError("too_large", "Source exceeds the readable byte limit.", root_id=root_id, relative_path=ref.relative_path)
            total = 0
            digest = hashlib.sha256()
            while True:
                self._check(cancellation, deadline, root_id, ref.relative_path)
                chunk = os.read(fd, min(STREAM_BUFFER_BYTES, MAX_READ_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_READ_BYTES:
                    raise LocalSourceError("too_large", "Source exceeds the readable byte limit.", root_id=root_id, relative_path=ref.relative_path)
                chunks.append(chunk)
                digest.update(chunk)
                if self._stream_hook:
                    self._stream_hook(total)
            post = os.fstat(fd)
        except OSError as exc:
            raise _os_error(exc, root_id, ref.relative_path)
        finally:
            os.close(fd)
        raw = b"".join(chunks)
        observation = self._stream_observation(root, ref, "content", pre, post, total, digest.hexdigest(), media_type="text/plain")
        bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            text = raw.decode("utf-8-sig" if bom else "utf-8")
        except UnicodeDecodeError as exc:
            raise LocalSourceError("unsupported_encoding", "Source is not valid UTF-8 text.", root_id=root_id, relative_path=ref.relative_path) from exc
        result = SourceReadResult(observation, text, "utf-8", bom)
        return result

    def read_bytes_for_extraction(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        configuration_revision: str | None = None, cancellation: CancellationToken | None = None,
        timeout_seconds: float = READ_HASH_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Read complete source bytes for a deterministic extractor, never truncated."""
        deadline = self._deadline(timeout_seconds, READ_HASH_TIMEOUT_SECONDS)
        root, ref = self._prepare(provider_namespace, root_id, relative_path, configuration_revision, cancellation, deadline)
        fd = self._open_content(root, ref.relative_path)
        chunks: list[bytes] = []
        try:
            pre = os.fstat(fd)
            self._require_regular(pre, root_id, ref.relative_path)
            if pre.st_size > MAX_EXTRACTION_BYTES:
                raise LocalSourceError("too_large", "Source exceeds the extraction byte limit.", root_id=root_id, relative_path=ref.relative_path)
            total = 0; digest = hashlib.sha256()
            while True:
                self._check(cancellation, deadline, root_id, ref.relative_path)
                chunk = os.read(fd, min(STREAM_BUFFER_BYTES, MAX_EXTRACTION_BYTES + 1 - total))
                if not chunk: break
                total += len(chunk)
                if total > MAX_EXTRACTION_BYTES:
                    raise LocalSourceError("too_large", "Source exceeds the extraction byte limit.", root_id=root_id, relative_path=ref.relative_path)
                chunks.append(chunk); digest.update(chunk)
                if self._stream_hook: self._stream_hook(total)
            post = os.fstat(fd)
        except OSError as exc:
            raise _os_error(exc, root_id, ref.relative_path)
        finally:
            os.close(fd)
        observation = self._stream_observation(root, ref, "content", pre, post, total, digest.hexdigest())
        return {"observation": observation.to_dict(), "raw": b"".join(chunks)}

    def acquire_managed_copy(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        destination_namespace: str, destination_root_id: str, canonical_extension: str,
        configuration_revision: str | None = None, destination_revision: str | None = None,
        cancellation: CancellationToken | None = None, timeout_seconds: float = READ_HASH_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Copy one stable source into a managed root while computing content identity.

        The original is opened through the retained source-root descriptor and is
        never modified. The destination is written as a temporary file, fsynced,
        then atomically linked to its canonical SHA-256 name without overwrite.
        """
        deadline = self._deadline(timeout_seconds, READ_HASH_TIMEOUT_SECONDS)
        source, ref = self._prepare(provider_namespace, root_id, relative_path, configuration_revision, cancellation, deadline)
        destination = self.registry.get(destination_namespace, destination_root_id, configuration_revision=destination_revision)
        if not destination.config.mutation_allowed:
            raise LocalSourceError("operation_not_allowed", "Managed destination is not writable.", root_id=destination_root_id)
        extension = str(canonical_extension or "").strip().lower()
        if extension and (not extension.startswith(".") or not re.fullmatch(r"\.[a-z0-9]{1,10}", extension)):
            raise LocalSourceError("invalid_path", "Canonical extension is invalid.", root_id=destination_root_id)
        source_fd = self._open_content(source, ref.relative_path)
        temp_name = f".incoming-{uuid.uuid4().hex}.part"
        temp_fd: int | None = None
        digest = hashlib.sha256(); total = 0
        try:
            pre = os.fstat(source_fd); self._require_regular(pre, root_id, ref.relative_path)
            if pre.st_size > MAX_HASH_BYTES:
                raise LocalSourceError("too_large", "Source exceeds the managed-intake byte limit.", root_id=root_id, relative_path=ref.relative_path)
            temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=destination.fd)
            while True:
                self._check(cancellation, deadline, root_id, ref.relative_path)
                chunk = os.read(source_fd, STREAM_BUFFER_BYTES)
                if not chunk: break
                total += len(chunk)
                if total > MAX_HASH_BYTES:
                    raise LocalSourceError("too_large", "Source exceeds the managed-intake byte limit.", root_id=root_id, relative_path=ref.relative_path)
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(temp_fd, view):]
                if self._stream_hook: self._stream_hook(total)
            os.fsync(temp_fd)
            post = os.fstat(source_fd)
            if _has_drift(pre, post):
                raise LocalSourceError("drifted", "Source changed during managed intake.", root_id=root_id, relative_path=ref.relative_path)
            content_sha256 = digest.hexdigest()
            storage_name = f"sha256-{content_sha256}{extension}"
            reused = False
            try:
                os.link(temp_name, storage_name, src_dir_fd=destination.fd, dst_dir_fd=destination.fd, follow_symlinks=False)
            except FileExistsError:
                reused = True
                existing_fd = os.open(storage_name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=destination.fd)
                try:
                    existing_hash = hashlib.sha256(); existing_size = 0
                    while True:
                        chunk = os.read(existing_fd, STREAM_BUFFER_BYTES)
                        if not chunk: break
                        existing_size += len(chunk); existing_hash.update(chunk)
                finally:
                    os.close(existing_fd)
                if existing_size != total or existing_hash.hexdigest() != content_sha256:
                    raise LocalSourceError("internal_invariant", "Managed content name collides with different bytes.", root_id=destination_root_id, relative_path=storage_name)
            os.unlink(temp_name, dir_fd=destination.fd); temp_name = ""
            os.fsync(destination.fd)
            observation = self._stream_observation(source, ref, "content", pre, post, total, content_sha256)
            return {"observation": observation.to_dict(), "content_sha256": content_sha256,
                    "byte_size": total, "managed_relative_path": storage_name, "reused_storage": reused}
        except OSError as exc:
            raise _os_error(exc, root_id, ref.relative_path)
        finally:
            if temp_fd is not None: os.close(temp_fd)
            os.close(source_fd)
            if temp_name:
                try: os.unlink(temp_name, dir_fd=destination.fd)
                except OSError: pass

    def materialize_derived_text(
        self, provider_namespace: str, root_id: str, text: str, *,
        configuration_revision: str | None = None, prefix: str = "representation",
    ) -> dict[str, Any]:
        """Materialize bounded UTF-8 derived text inside the managed derived area."""
        root = self.registry.get(provider_namespace, root_id, configuration_revision=configuration_revision)
        if root.derived_fd is None:
            raise LocalSourceError("operation_not_allowed", "Managed derived area is unavailable.", root_id=root_id)
        payload = text.encode("utf-8")
        if len(payload) > MAX_READ_BYTES:
            raise LocalSourceError("too_large", "Derived text exceeds the readable byte limit.", root_id=root_id)
        clean_prefix = re.sub(r"[^A-Za-z0-9._-]+", "-", prefix).strip("-") or "representation"
        name = f"{clean_prefix}-{uuid.uuid4().hex}.txt"
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=root.derived_fd)
        except OSError as exc:
            raise _os_error(exc, root_id, name)
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(root.derived_fd)
        return {
            "derived_relative_path": f"{root.config.derived_relative_path}/{name}",
            "text_sha256": hashlib.sha256(payload).hexdigest(), "byte_length": len(payload),
        }

    def extract_text(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        extractor: str, configuration_revision: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> dict[str, Any]:
        """Materialize one deterministic text representation in the managed derived area.

        Text extractors use the ordinary bounded UTF-8 path. Byte extractors get a
        separate complete-read path capped at MAX_EXTRACTION_BYTES; extraction never
        proceeds from a truncated source.
        """
        from .extractors import EXTRACTOR_INPUT, ExtractorUnavailable, extract as _extract

        input_kind = EXTRACTOR_INPUT.get(extractor)
        if input_kind is None:
            raise LocalSourceError("operation_not_allowed", f"Unsupported extractor: {extractor}", root_id=root_id, relative_path=relative_path)
        if input_kind == "bytes":
            acquired = self.read_bytes_for_extraction(
                provider_namespace, root_id, relative_path,
                configuration_revision=configuration_revision, cancellation=cancellation,
            )
            observation = acquired["observation"]; value = acquired["raw"]
        else:
            source = self.read(
                provider_namespace, root_id, relative_path,
                configuration_revision=configuration_revision, cancellation=cancellation,
            )
            observation = source.observation.to_dict(); value = source.text
        root = self.registry.get(provider_namespace, root_id, configuration_revision=configuration_revision)
        if root.derived_fd is None:
            raise LocalSourceError("operation_not_allowed", "Managed derived area is unavailable.", root_id=root_id, relative_path=relative_path)
        try:
            text = _extract(value, extractor)
        except ExtractorUnavailable as exc:
            raise LocalSourceError("extractor_unavailable", str(exc), root_id=root_id, relative_path=relative_path) from exc
        payload = text.encode("utf-8")
        if len(payload) > MAX_READ_BYTES:
            raise LocalSourceError("too_large", "Extracted text exceeds the readable byte limit.", root_id=root_id, relative_path=relative_path)
        name = f"extract-{uuid.uuid4().hex}.txt"
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=root.derived_fd)
        except OSError as exc:
            raise _os_error(exc, root_id, name)
        try:
            view = memoryview(payload)
            while view: view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(root.derived_fd)
        derived_relative = f"{root.config.derived_relative_path}/{name}"
        return {
            "observation": observation, "extractor": extractor, "derived_relative_path": derived_relative,
            "text_sha256": hashlib.sha256(payload).hexdigest(), "byte_length": len(payload),
            "source_byte_sha256": observation.get("byte_sha256"),
        }

    def list(
        self, provider_namespace: str, root_id: str, relative_path: str, *,
        page_size: int = DEFAULT_LIST_PAGE_SIZE, cursor: str | None = None,
        configuration_revision: str | None = None, cancellation: CancellationToken | None = None,
        timeout_seconds: float = LIST_STAT_TIMEOUT_SECONDS,
    ) -> SourceListResult:
        if not 1 <= page_size <= MAX_LIST_PAGE_SIZE:
            raise LocalSourceError("invalid_path", f"page_size must be between 1 and {MAX_LIST_PAGE_SIZE}.", root_id=root_id, relative_path=relative_path)
        deadline = self._deadline(timeout_seconds, LIST_STAT_TIMEOUT_SECONDS)
        root, ref = self._prepare(provider_namespace, root_id, relative_path, configuration_revision, cancellation, deadline)
        fd = self._open_directory(root, ref.relative_path)
        try:
            pre = os.fstat(fd)
            names = os.listdir(fd)
            valid_names: list[str] = []
            entry_errors: list[dict[str, Any]] = []
            for name in names:
                self._check(cancellation, deadline, root_id, ref.relative_path)
                try:
                    validate_component(name)
                    name.encode("utf-8", "strict")
                except (LocalSourceError, UnicodeError):
                    entry_errors.append({"code": "invalid_path", "message": "An entry name is not representable by local-files-v1."})
                    continue
                quarantine = root.config.quarantine_relative_path
                quarantine_parent, quarantine_name = (
                    quarantine.rsplit("/", 1) if quarantine and "/" in quarantine
                    else (".", quarantine)
                )
                if (
                    quarantine_name is not None
                    and ref.relative_path == quarantine_parent
                    and name == quarantine_name
                ):
                    continue
                valid_names.append(name)
            valid_names.sort(key=lambda item: item.encode("utf-8"))
            fingerprint = _stat_fingerprint(pre)
            start = 0
            if cursor:
                cursor_data = self._decode_cursor(cursor, root_id, ref.relative_path)
                expected = {
                    "policy": FILESYSTEM_POLICY_VERSION, "provider_namespace": provider_namespace,
                    "root_id": root_id, "relative_path": ref.relative_path,
                    "page_size": page_size, "directory_fingerprint": fingerprint,
                }
                if any(cursor_data.get(key) != value for key, value in expected.items()):
                    raise LocalSourceError("drifted", "Directory changed or cursor does not match this listing.", root_id=root_id, relative_path=ref.relative_path)
                last = cursor_data.get("last_name")
                if not isinstance(last, str):
                    raise LocalSourceError("invalid_path", "Listing cursor is invalid.", root_id=root_id, relative_path=ref.relative_path)
                start = next((i for i, name in enumerate(valid_names) if name.encode("utf-8") > last.encode("utf-8")), len(valid_names))
            selected = valid_names[start:start + page_size]
            entries: list[SourceObservation] = []
            for name in selected:
                self._check(cancellation, deadline, root_id, ref.relative_path)
                entry_path = name if ref.relative_path == "." else f"{ref.relative_path}/{name}"
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    entry_errors.append({"code": "missing", "relative_path": entry_path, "message": "Entry disappeared during listing."})
                    continue
                except PermissionError:
                    entry_errors.append({"code": "permission_denied", "relative_path": entry_path, "message": "Entry metadata is unavailable."})
                    continue
                if info.st_dev != root.device:
                    entry_errors.append({"code": "outside_root", "relative_path": entry_path, "message": "Entry crosses a filesystem boundary."})
                    continue
                entry_ref = self.source_ref(provider_namespace, root_id, entry_path)
                entries.append(SourceObservation.create(
                    source_ref=entry_ref, observed_at=_utc_now(), observation_kind="listing_entry",
                    object_type=_object_type(info.st_mode), consistency="metadata_only", completeness="metadata_only",
                    byte_size=info.st_size if stat_module.S_ISREG(info.st_mode) else None,
                    metadata=_stat_metadata(info), acquisition=self._acquisition(root, "list"),
                ))
            post = os.fstat(fd)
        except OSError as exc:
            raise _os_error(exc, root_id, ref.relative_path)
        finally:
            os.close(fd)
        drifted = _has_drift(pre, post) or bool(entry_errors)
        has_more = start + len(selected) < len(valid_names)
        next_cursor = None
        if has_more and selected and not drifted:
            next_cursor = self._encode_cursor({
                "policy": FILESYSTEM_POLICY_VERSION, "provider_namespace": provider_namespace,
                "root_id": root_id, "relative_path": ref.relative_path, "page_size": page_size,
                "directory_fingerprint": fingerprint, "last_name": selected[-1],
            })
        observation = SourceObservation.create(
            source_ref=ref, observed_at=_utc_now(), observation_kind="listing", object_type="directory",
            consistency="drifted" if drifted else "stable",
            completeness="bounded" if has_more else "complete",
            metadata={
                "pre": _stat_metadata(pre), "post": _stat_metadata(post),
                "entry_observation_ids": [item.observation_id for item in entries],
                "entry_errors": entry_errors, "page_size": page_size,
            }, acquisition=self._acquisition(root, "list"),
        )
        result = SourceListResult(observation, tuple(entries), next_cursor, tuple(entry_errors))
        self._enforce_metadata(observation.metadata, root_id, ref.relative_path)
        self._enforce_result(result.to_dict(), root_id, ref.relative_path)
        if drifted:
            raise LocalSourceError(
                "drifted", "Directory changed during listing.", root_id=root_id,
                relative_path=ref.relative_path, details={"result": result.to_dict()},
            )
        return result

    def _prepare(self, provider_namespace: str, root_id: str, relative_path: str, revision: str | None, cancellation: CancellationToken | None, deadline: float) -> tuple[_RegisteredRoot, SourceRef]:
        path = validate_relative_path(relative_path)
        root = self.registry.get(provider_namespace, root_id, configuration_revision=revision)
        if not root.config.read_allowed:
            raise LocalSourceError("operation_not_allowed", "Read access is not enabled for this root.", root_id=root_id, relative_path=path)
        quarantine = root.config.quarantine_relative_path
        if quarantine is not None and (path == quarantine or path.startswith(quarantine + "/")):
            raise LocalSourceError(
                "operation_not_allowed",
                "Managed quarantine is reserved from ordinary source access.",
                root_id=root_id,
                relative_path=path,
            )
        self._check(cancellation, deadline, root_id, path)
        return root, self.source_ref(provider_namespace, root_id, path)

    def _open_content(self, root: _RegisteredRoot, path: str) -> int:
        probe_flags = getattr(os, "O_PATH", os.O_RDONLY) | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            probe = self._open_beneath(root, path, probe_flags)
            try:
                info = os.fstat(probe)
                self._require_regular(info, root.config.root_id, path)
                # Reopen the already pinned regular object, never its pathname. This
                # prevents a pathname replacement between classification and read.
                fd = os.open(f"/proc/self/fd/{probe}", os.O_RDONLY | os.O_CLOEXEC)
                opened = os.fstat(fd)
                if (info.st_dev, info.st_ino, stat_module.S_IFMT(info.st_mode)) != (
                    opened.st_dev, opened.st_ino, stat_module.S_IFMT(opened.st_mode)
                ):
                    os.close(fd)
                    raise LocalSourceError("internal_invariant", "Pinned source identity changed while opening.", root_id=root.config.root_id, relative_path=path)
                return fd
            finally:
                os.close(probe)
        except LocalSourceError:
            raise
        except OSError as exc:
            raise _os_error(exc, root.config.root_id, path)

    def _open_directory(self, root: _RegisteredRoot, path: str) -> int:
        try:
            return self._open_beneath(root, path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY)
        except LocalSourceError:
            raise
        except OSError as exc:
            raise _os_error(exc, root.config.root_id, path)

    def _open_beneath(self, root: _RegisteredRoot, path: str, flags: int) -> int:
        if path == ".":
            return os.dup(root.fd)
        if self._openat2_available:
            how = _OpenHow(flags=flags, mode=0, resolve=_OPENAT2_RESOLVE)
            result = self._libc.syscall(_OPENAT2_SYSCALL, root.fd, path.encode("utf-8"), ctypes.byref(how), ctypes.sizeof(how))
            if result >= 0:
                return int(result)
            error = ctypes.get_errno()
            if error not in {errno.ENOSYS, errno.EINVAL}:
                raise _os_error(OSError(error, os.strerror(error)), root.config.root_id, path)
            self._openat2_available = False
        return self._openat_fallback(root, path, flags)

    def _openat_fallback(self, root: _RegisteredRoot, path: str, final_flags: int) -> int:
        current = os.dup(root.fd)
        try:
            components = path.split("/")
            for component in components[:-1]:
                nxt = os.open(component, getattr(os, "O_PATH", os.O_RDONLY) | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=current)
                info = os.fstat(nxt)
                if stat_module.S_ISLNK(info.st_mode):
                    os.close(nxt)
                    raise LocalSourceError("symlink_rejected", "Symlink traversal is not allowed.", root_id=root.config.root_id, relative_path=path)
                if not stat_module.S_ISDIR(info.st_mode):
                    os.close(nxt)
                    raise LocalSourceError("wrong_type", "An intermediate source component is not a directory.", root_id=root.config.root_id, relative_path=path)
                if info.st_dev != root.device:
                    os.close(nxt)
                    raise LocalSourceError("outside_root", "Cross-mount traversal is not allowed.", root_id=root.config.root_id, relative_path=path)
                os.close(current)
                current = nxt
            result = os.open(components[-1], final_flags, dir_fd=current)
            info = os.fstat(result)
            if info.st_dev != root.device:
                os.close(result)
                raise LocalSourceError("outside_root", "Cross-mount traversal is not allowed.", root_id=root.config.root_id, relative_path=path)
            return result
        finally:
            os.close(current)

    def _lstat(self, root: _RegisteredRoot, path: str) -> os.stat_result:
        if path == ".":
            return os.fstat(root.fd)
        parent, name = path.rsplit("/", 1) if "/" in path else (".", path)
        parent_fd = self._open_directory(root, parent)
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if info.st_dev != root.device:
                raise LocalSourceError("outside_root", "Cross-mount traversal is not allowed.", root_id=root.config.root_id, relative_path=path)
            return info
        except OSError as exc:
            raise _os_error(exc, root.config.root_id, path)
        finally:
            os.close(parent_fd)

    def _stream_observation(self, root: _RegisteredRoot, ref: SourceRef, kind: str, pre: os.stat_result, post: os.stat_result, total: int, digest: str, media_type: str | None = None) -> SourceObservation:
        drifted = _has_drift(pre, post) or total != post.st_size
        observation = SourceObservation.create(
            source_ref=ref, observed_at=_utc_now(), observation_kind=kind, object_type="regular_file",
            consistency="drifted" if drifted else "stable", completeness="complete",
            byte_size=total, byte_sha256=None if drifted else digest,
            media_type=media_type, media_type_source="utf8_validation" if media_type else None,
            metadata={"pre": _stat_metadata(pre), "post": _stat_metadata(post), **({"diagnostic_digest": digest} if drifted else {})},
            acquisition=self._acquisition(root, kind),
        )
        self._enforce_metadata(observation.metadata, root.config.root_id, ref.relative_path)
        self._enforce_result(observation.to_dict(), root.config.root_id, ref.relative_path)
        if drifted:
            raise LocalSourceError("drifted", "Source changed during acquisition.", root_id=root.config.root_id, relative_path=ref.relative_path, details={"observation": observation.to_dict()})
        return observation

    def _require_regular(self, info: os.stat_result, root_id: str, path: str) -> None:
        object_type = _object_type(info.st_mode)
        if object_type == "symlink":
            raise LocalSourceError("symlink_rejected", "Symlinks are not followed for content acquisition.", root_id=root_id, relative_path=path)
        if object_type in {"socket", "fifo", "block_device", "character_device", "unknown"}:
            raise LocalSourceError("special_object_rejected", "Special objects cannot be read or hashed.", root_id=root_id, relative_path=path)
        if object_type != "regular_file":
            raise LocalSourceError("wrong_type", "Operation requires a regular file.", root_id=root_id, relative_path=path)

    def _acquisition(self, root: _RegisteredRoot, operation: str) -> dict[str, Any]:
        return {
            "provider_namespace": root.config.provider_namespace,
            "root_id": root.config.root_id,
            "configuration_revision": root.config.configuration_revision,
            "operation": operation,
            "filesystem_policy_version": FILESYSTEM_POLICY_VERSION,
            "backend": (
                "linux_openat2"
                if self._openat2_available
                else "linux_openat_fallback"
            ),
        }

    def _deadline(self, timeout_seconds: float, kernel_maximum: float) -> float:
        if timeout_seconds <= 0:
            return self._clock()
        return self._clock() + min(timeout_seconds, kernel_maximum)

    def _check(self, cancellation: CancellationToken | None, deadline: float, root_id: str, path: str) -> None:
        if cancellation is not None and cancellation.cancelled:
            raise LocalSourceError("cancelled", "Source acquisition was cancelled.", root_id=root_id, relative_path=path)
        if self._clock() >= deadline:
            raise LocalSourceError("timeout", "Source acquisition exceeded its deadline.", root_id=root_id, relative_path=path)

    def _enforce_metadata(self, value: Any, root_id: str, path: str) -> None:
        if _json_size(value) > MAX_METADATA_BYTES:
            raise LocalSourceError("too_large", "Observation metadata exceeds the kernel limit.", root_id=root_id, relative_path=path)

    def _enforce_result(self, value: Any, root_id: str, path: str) -> None:
        if _json_size(value) > MAX_RESULT_BYTES:
            raise LocalSourceError("too_large", "Serialized acquisition result exceeds the kernel limit.", root_id=root_id, relative_path=path)

    def _encode_cursor(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        signature = hmac.new(self._cursor_secret, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + signature).decode("ascii").rstrip("=")

    def _decode_cursor(self, cursor: str, root_id: str, path: str) -> dict[str, Any]:
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload, signature = raw[:-32], raw[-32:]
            if len(signature) != 32 or not hmac.compare_digest(signature, hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()):
                raise ValueError
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LocalSourceError("invalid_path", "Listing cursor is invalid.", root_id=root_id, relative_path=path) from exc


def validate_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise LocalSourceError("invalid_path", "Relative path must not be empty.")
    if len(value.encode("utf-8")) > MAX_PATH_BYTES:
        raise LocalSourceError("invalid_path", "Relative path exceeds the v1 byte limit.")
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise LocalSourceError("invalid_path", "Relative path must use canonical root-relative separators.", relative_path=_safe_path(value))
    if "\\" in value or _WINDOWS_DRIVE.match(value):
        raise LocalSourceError("invalid_path", "Windows path syntax is not supported.", relative_path=_safe_path(value))
    if unicodedata.normalize("NFC", value) != value:
        raise LocalSourceError("invalid_path", "Relative path must already be Unicode NFC.", relative_path=_safe_path(value))
    if value == ".":
        return value
    for component in value.split("/"):
        validate_component(component)
    return value


def validate_component(component: str) -> None:
    if not component or component in {".", ".."}:
        raise LocalSourceError("invalid_path", "Relative path contains a reserved component.")
    if any(ord(char) < 32 or ord(char) == 127 for char in component):
        raise LocalSourceError("invalid_path", "Relative path contains a control character.")
    if "\x00" in component:
        raise LocalSourceError("invalid_path", "Relative path contains NUL.")
    if unicodedata.normalize("NFC", component) != component:
        raise LocalSourceError("invalid_path", "Relative path must already be Unicode NFC.")


def _open_retained_directory(
    root_fd: int, path: str, root_device: int, root_id: str
) -> int:
    """Open configured quarantine through descriptors without following links."""

    current = os.dup(root_fd)
    try:
        for component in path.split("/"):
            try:
                entry = os.stat(component, dir_fd=current, follow_symlinks=False)
                if stat_module.S_ISLNK(entry.st_mode):
                    raise LocalSourceError(
                        "symlink_rejected",
                        "Managed quarantine must not contain symlinks.",
                        root_id=root_id,
                    )
                nxt = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=current,
                )
            except LocalSourceError:
                raise
            except OSError as exc:
                raise _os_error(exc, root_id, path)
            info = os.fstat(nxt)
            if info.st_dev != root_device:
                os.close(nxt)
                raise LocalSourceError(
                    "outside_root",
                    "Managed quarantine must be on the root filesystem.",
                    root_id=root_id,
                )
            os.close(current)
            current = nxt
        return current
    except BaseException:
        os.close(current)
        raise


def _safe_path(value: str) -> str | None:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value[:MAX_PATH_BYTES]


def _object_type(mode: int) -> ObjectType:
    if stat_module.S_ISREG(mode): return "regular_file"
    if stat_module.S_ISDIR(mode): return "directory"
    if stat_module.S_ISLNK(mode): return "symlink"
    if stat_module.S_ISSOCK(mode): return "socket"
    if stat_module.S_ISFIFO(mode): return "fifo"
    if stat_module.S_ISBLK(mode): return "block_device"
    if stat_module.S_ISCHR(mode): return "character_device"
    return "unknown"


def _stat_metadata(info: os.stat_result) -> dict[str, Any]:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": stat_module.S_IMODE(info.st_mode),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "nlink": info.st_nlink,
    }


def _stat_fingerprint(info: os.stat_result) -> str:
    payload = json.dumps(_stat_metadata(info), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _has_drift(before: os.stat_result, after: os.stat_result) -> bool:
    return any((
        before.st_dev != after.st_dev,
        before.st_ino != after.st_ino,
        stat_module.S_IFMT(before.st_mode) != stat_module.S_IFMT(after.st_mode),
        before.st_size != after.st_size,
        before.st_mtime_ns != after.st_mtime_ns,
        before.st_ctime_ns != after.st_ctime_ns,
    ))


def _os_error(exc: OSError, root_id: str | None, path: str | None) -> LocalSourceError:
    code: LocalSourceErrorCode
    if exc.errno in {errno.ENOENT}: code = "missing"
    elif exc.errno in {errno.EACCES, errno.EPERM}: code = "permission_denied"
    elif exc.errno in {errno.ELOOP}: code = "symlink_rejected"
    elif exc.errno in {errno.EXDEV}: code = "outside_root"
    elif exc.errno in {errno.ENOTDIR, errno.EISDIR}: code = "wrong_type"
    elif exc.errno in {errno.ENAMETOOLONG, errno.EINVAL}: code = "invalid_path"
    elif exc.errno in {errno.ENXIO, errno.ENODEV}: code = "special_object_rejected"
    else: code = "unreadable"
    messages = {
        "missing": "Source does not exist.", "permission_denied": "Source access was denied.",
        "symlink_rejected": "Symlink traversal is not allowed.", "outside_root": "Source traversal left the configured root.",
        "wrong_type": "Source has the wrong object type.", "invalid_path": "Source path is invalid.",
        "special_object_rejected": "Special object access is not allowed.", "unreadable": "Source could not be acquired.",
    }
    return LocalSourceError(code, messages[code], root_id=root_id, relative_path=path)
