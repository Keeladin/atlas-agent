from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretStoreError(ValueError):
    pass


@dataclass(frozen=True)
class CredentialMetadata:
    credential_ref: str
    kind: str
    status: str
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, str]:
        return self.__dict__.copy()


class CredentialStore:
    """Narrow encrypted-at-rest secret boundary for the local deployment."""

    def __init__(self, instance_root: str | Path) -> None:
        root = Path(instance_root)
        self.root = root / "secrets"
        self.key_path = self.root / "master.key"
        self.db_path = self.root / "credentials.db"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # State is shared only by the owner shell and the atlas service account.
        # The parent instance directory is setgid atlas in production; preserve
        # that group and deny access to everyone else.
        os.chmod(self.root, 0o770)
        if not self.key_path.exists():
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660)
            with os.fdopen(fd, "wb") as handle:
                handle.write(os.urandom(32))
        os.chmod(self.key_path, 0o660)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS credentials (
                credential_ref TEXT PRIMARY KEY, kind TEXT NOT NULL,
                nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active','disabled')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        os.chmod(self.db_path, 0o660)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _key(self) -> bytes:
        value = self.key_path.read_bytes()
        if len(value) != 32:
            raise SecretStoreError("credential master key is invalid")
        return value

    def create(self, *, kind: str, secret: dict[str, Any], credential_ref: str | None = None) -> str:
        ref = credential_ref or f"credential_{uuid4().hex}"
        nonce = os.urandom(12)
        plain = json.dumps(secret, sort_keys=True, separators=(",", ":")).encode()
        encrypted = AESGCM(self._key()).encrypt(nonce, plain, ref.encode())
        with self._db() as db:
            db.execute(
                "INSERT INTO credentials (credential_ref,kind,nonce,ciphertext,status) VALUES (?,?,?,?,'active')",
                (ref, kind, nonce, encrypted),
            )
        return ref

    def replace(self, credential_ref: str, secret: dict[str, Any]) -> None:
        self.inspect(credential_ref)
        nonce = os.urandom(12)
        plain = json.dumps(secret, sort_keys=True, separators=(",", ":")).encode()
        encrypted = AESGCM(self._key()).encrypt(nonce, plain, credential_ref.encode())
        with self._db() as db:
            db.execute(
                "UPDATE credentials SET nonce=?,ciphertext=?,status='active',updated_at=CURRENT_TIMESTAMP WHERE credential_ref=?",
                (nonce, encrypted, credential_ref),
            )

    def retrieve(self, credential_ref: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM credentials WHERE credential_ref=?", (credential_ref,)).fetchone()
        if row is None:
            raise SecretStoreError("credential_unknown")
        if row["status"] != "active":
            raise SecretStoreError("credential_disabled")
        try:
            plain = AESGCM(self._key()).decrypt(
                bytes(row["nonce"]), bytes(row["ciphertext"]), credential_ref.encode()
            )
            value = json.loads(plain)
        except Exception as exc:
            raise SecretStoreError("credential_decryption_failed") from exc
        if not isinstance(value, dict):
            raise SecretStoreError("credential_payload_invalid")
        return value

    def inspect(self, credential_ref: str) -> CredentialMetadata:
        with self._db() as db:
            row = db.execute(
                "SELECT credential_ref,kind,status,created_at,updated_at FROM credentials WHERE credential_ref=?",
                (credential_ref,),
            ).fetchone()
        if row is None:
            raise SecretStoreError("credential_unknown")
        return CredentialMetadata(**dict(row))

    def disable(self, credential_ref: str) -> None:
        with self._db() as db:
            changed = db.execute(
                "UPDATE credentials SET status='disabled',updated_at=CURRENT_TIMESTAMP WHERE credential_ref=?",
                (credential_ref,),
            ).rowcount
        if not changed:
            raise SecretStoreError("credential_unknown")

    def delete(self, credential_ref: str) -> None:
        with self._db() as db:
            changed = db.execute("DELETE FROM credentials WHERE credential_ref=?", (credential_ref,)).rowcount
        if not changed:
            raise SecretStoreError("credential_unknown")
