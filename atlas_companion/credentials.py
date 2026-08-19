from __future__ import annotations

import os
from pathlib import Path


class CredentialStore:
    """Host-local provider secrets. Never returned to the browser or written to overlay JSON."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "secrets" / "providers"

    def path_for(self, provider_key: str) -> Path:
        safe = provider_key.replace("/", "_")
        return self.root / f"{safe}.key"

    def configured(self, provider_key: str) -> bool:
        return self.path_for(provider_key).is_file()

    def get(self, provider_key: str) -> str | None:
        path = self.path_for(provider_key)
        if not path.is_file():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None

    def put(self, provider_key: str, secret: str) -> None:
        value = (secret or "").strip()
        if not value:
            raise ValueError("Credential must not be empty.")
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        path = self.path_for(provider_key)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
        os.chmod(path, 0o600)

    def delete(self, provider_key: str) -> None:
        path = self.path_for(provider_key)
        if path.is_file():
            path.unlink()

    def apply_env(self, provider_key: str, env_name: str | None) -> bool:
        if not env_name:
            return False
        value = self.get(provider_key)
        if not value:
            return bool(os.environ.get(env_name))
        os.environ[env_name] = value
        return True
