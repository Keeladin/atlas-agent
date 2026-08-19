from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen

from atlas_companion.cloud_providers import ProviderStateStore


COMPOSE_DIR = Path("/srv/atlas/inference")
MODELS_ROOT = Path("/srv/atlas/models")

LOCAL_SLOTS = {
    "local:resident": {
        "id": "local:resident",
        "name": "Qwen3.5 9B",
        "service": "atlas-inference",
        "container": "atlas-inference",
        "alias": "atlas",
        "endpoint": "http://127.0.0.1:1234",
        "gguf": "lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q6_K.gguf",
        "quantization": "Q6_K",
        "context": 8192,
        "n_gpu_layers": 999,
        "uses_gpu": True,
        "model": "atlas",
    },
    "local:heavy": {
        "id": "local:heavy",
        "name": "Qwen3.5 35B-A3B",
        "service": "atlas-inference-heavy",
        "container": "atlas-inference-heavy",
        "alias": "atlas-heavy",
        "endpoint": "http://127.0.0.1:1235",
        "gguf": "mikaelharut/Qwen3.5-35B-A3B-Q4_K_M-GGUF/qwen3.5-35b-a3b-q4_k_m.gguf",
        "quantization": "Q4_K_M",
        "context": 8192,
        "n_gpu_layers": 8,
        "uses_gpu": True,
        "model": "atlas-heavy",
    },
}

_QUANT_RE = re.compile(r"(Q\d+_K(?:_[MSL])?)", re.IGNORECASE)


class LocalModelError(ValueError):
    pass


class InferenceHost:
    """Docker/compose/health operations. Tests replace this object."""

    def __init__(self, compose_dir: Path = COMPOSE_DIR, models_root: Path = MODELS_ROOT) -> None:
        self.compose_dir = Path(compose_dir)
        self.models_root = Path(models_root)

    def _run(self, args: list[str], *, cwd: Path | None = None, timeout: int = 120) -> str:
        try:
            result = subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LocalModelError(f"Failed to run {' '.join(args)}.") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise LocalModelError(detail[-1] if detail else f"Command failed: {' '.join(args)}")
        return (result.stdout or "").strip()

    def container_state(self, name: str) -> dict[str, Any]:
        try:
            raw = self._run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .State}}",
                    name,
                ],
                timeout=10,
            )
        except LocalModelError:
            return {"exists": False, "running": False, "status": "missing", "health": None, "exit_code": None}
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            return {"exists": True, "running": False, "status": "unknown", "health": None, "exit_code": None}
        health = None
        if isinstance(state.get("Health"), dict):
            health = state["Health"].get("Status")
        return {
            "exists": True,
            "running": bool(state.get("Running")),
            "status": str(state.get("Status") or "unknown"),
            "health": health,
            "exit_code": state.get("ExitCode"),
        }

    def compose(self, *args: str) -> None:
        self._run(["docker", "compose", *args], cwd=self.compose_dir, timeout=180)

    def endpoint_healthy(self, url: str) -> bool:
        try:
            with urlopen(url.rstrip("/") + "/health", timeout=2) as response:
                return 200 <= response.status < 300
        except (OSError, URLError):
            return False

    def gpu_memory(self) -> tuple[int | None, int | None]:
        try:
            raw = self._run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                timeout=5,
            )
        except LocalModelError:
            return None, None
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) < 2:
            return None, None
        try:
            return int(float(parts[0])), int(float(parts[1]))
        except ValueError:
            return None, None

    def list_gguf(self) -> list[dict[str, Any]]:
        if not self.models_root.is_dir():
            return []
        rows = []
        for path in sorted(self.models_root.rglob("*.gguf")):
            rel = str(path.relative_to(self.models_root))
            match = _QUANT_RE.search(path.name)
            rows.append(
                {
                    "path": rel,
                    "name": path.name,
                    "quantization": match.group(1).upper() if match else None,
                    "bytes": path.stat().st_size if path.is_file() else None,
                }
            )
        return rows


class LocalModelManager:
    def __init__(
        self,
        *,
        host: InferenceHost | None = None,
        state: ProviderStateStore | None = None,
        reload_router: Callable[[], None] | None = None,
        wait: Callable[[], None] | None = None,
    ) -> None:
        self.host = host or InferenceHost()
        self.state = state
        self.reload_router = reload_router
        self._wait = wait

    def catalog(self) -> list[dict[str, Any]]:
        used, total = self.host.gpu_memory()
        slots = [self._slot_status(slot) for slot in LOCAL_SLOTS.values()]
        mapped = {slot["gguf"] for slot in LOCAL_SLOTS.values()}
        extras = [
            {
                "id": None,
                "name": item["name"],
                "path": item["path"],
                "quantization": item["quantization"],
                "status": "available",
                "loadable": False,
                "bytes": item["bytes"],
            }
            for item in self.host.list_gguf()
            if item["path"] not in mapped
        ]
        return {
            "slots": slots,
            "gpu": {"memory_used_mib": used, "memory_total_mib": total},
            "unmapped_gguf": extras,
        }

    def _slot_status(self, slot: dict[str, Any]) -> dict[str, Any]:
        state = self.host.container_state(slot["container"])
        healthy = self.host.endpoint_healthy(slot["endpoint"]) if state.get("running") else False
        if not state.get("exists"):
            status = "available"
        elif state.get("running") and healthy:
            status = "loaded"
        elif state.get("running"):
            status = "loading"
        elif state.get("exit_code") not in {None, 0} and state.get("status") == "exited":
            status = "failed"
        else:
            status = "stopped"
        path = self.host.models_root / slot["gguf"]
        return {
            "id": slot["id"],
            "name": slot["name"],
            "alias": slot["alias"],
            "model": slot["model"],
            "service": slot["service"],
            "endpoint": slot["endpoint"],
            "quantization": slot["quantization"],
            "context": slot["context"],
            "n_gpu_layers": slot["n_gpu_layers"],
            "gguf": slot["gguf"],
            "on_disk": path.is_file() if self.host.models_root.is_dir() else None,
            "status": status,
            "container_status": state.get("status"),
            "health": "healthy" if healthy else state.get("health"),
            "loadable": True,
            "uses_gpu": slot["uses_gpu"],
        }

    def loaded_gpu_ids(self) -> list[str]:
        loaded = []
        for slot in LOCAL_SLOTS.values():
            if not slot["uses_gpu"]:
                continue
            info = self._slot_status(slot)
            if info["status"] in {"loaded", "loading"}:
                loaded.append(slot["id"])
        return loaded

    def unload(self, slot_id: str) -> dict[str, Any]:
        slot = _require_slot(slot_id)
        if self.host.container_state(slot["container"]).get("exists"):
            self.host.compose("stop", slot["service"])
        if self.state is not None:
            self.state.update(slot_id, enabled=False)
        if self.reload_router:
            self.reload_router()
        return self._slot_status(slot)

    def load(self, slot_id: str) -> dict[str, Any]:
        others = [item for item in self.loaded_gpu_ids() if item != slot_id]
        if others:
            raise LocalModelError(
                "Another local GPU model is loaded. Activate this model to swap sequentially."
            )
        return self._start(slot_id, exclusive=False)

    def activate(self, slot_id: str) -> dict[str, Any]:
        return self._start(slot_id, exclusive=True)

    def _start(self, slot_id: str, *, exclusive: bool) -> dict[str, Any]:
        slot = _require_slot(slot_id)
        if exclusive:
            for other_id, other in LOCAL_SLOTS.items():
                if other_id == slot_id or not other["uses_gpu"]:
                    continue
                if self.host.container_state(other["container"]).get("running"):
                    self.host.compose("stop", other["service"])
                if self.state is not None:
                    self.state.update(other_id, enabled=False)
        exists = self.host.container_state(slot["container"]).get("exists")
        if exists:
            self.host.compose("start", slot["service"])
        else:
            self.host.compose("up", "-d", slot["service"])
        self._wait_healthy(slot)
        if self.state is not None:
            self.state.update(
                slot_id,
                enabled=True,
                model=slot["model"],
                base_url=slot["endpoint"],
            )
        if self.reload_router:
            self.reload_router()
        return self._slot_status(slot)

    def _wait_healthy(self, slot: dict[str, Any]) -> None:
        if self._wait is not None:
            self._wait()
            if not self.host.endpoint_healthy(slot["endpoint"]):
                raise LocalModelError(f"{slot['id']} started but health check failed.")
            return
        for _ in range(40):
            if self.host.endpoint_healthy(slot["endpoint"]):
                return
            self._sleep()
        raise LocalModelError(f"{slot['id']} did not become healthy.")

    @staticmethod
    def _sleep() -> None:
        import time
        time.sleep(0.5)


def _require_slot(slot_id: str) -> dict[str, Any]:
    try:
        return LOCAL_SLOTS[slot_id]
    except KeyError as exc:
        raise LocalModelError(f"Unknown local model: {slot_id}") from exc
