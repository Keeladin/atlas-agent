"""Best-effort host telemetry for the LAN-only Companion service."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def _command(*args: str) -> str | None:
    try:
        return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=3, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _bytes(path: Path) -> int | None:
    try:
        if path.is_file(): return path.stat().st_size
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError:
        return None


def _memory() -> dict[str, int | None]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    return {"used_bytes": total - available if total is not None and available is not None else None,
            "total_bytes": total, "swap_used_bytes": values.get("SwapTotal", 0) - values.get("SwapFree", 0),
            "swap_total_bytes": values.get("SwapTotal")}


def _gpu() -> dict[str, object]:
    fields = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,pstate"
    output = _command("nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits")
    if not output: return {"available": False}
    parts = [part.strip() for part in output.splitlines()[0].split(",")]
    if len(parts) != 8: return {"available": False}
    processes = _command("nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits")
    return {"available": True, "name": parts[0], "utilization_percent": _number(parts[1]),
            "memory_used_mib": _number(parts[2]), "memory_total_mib": _number(parts[3]),
            "temperature_c": _number(parts[4]), "power_draw_w": _number(parts[5]),
            "power_limit_w": _number(parts[6]), "performance_state": parts[7],
            "processes": [_gpu_process(row) for row in (processes or "").splitlines() if row]}


def _number(value: str) -> float | int | None:
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except ValueError: return None


def _gpu_process(row: str) -> dict[str, object]:
    parts = [part.strip() for part in row.split(",", 2)]
    return {"pid": _number(parts[0]), "name": parts[1] if len(parts) > 1 else "unknown",
            "memory_mib": _number(parts[2]) if len(parts) > 2 else None}


def _docker() -> list[dict[str, str]] | None:
    output = _command("docker", "ps", "-a", "--format", "{{json .}}")
    if output is None: return None
    result = []
    for line in output.splitlines():
        try:
            row = json.loads(line)
            result.append({"name": row.get("Names", "unknown"), "state": row.get("State", "unknown"),
                           "status": row.get("Status", "unknown"), "image": row.get("Image", "")})
        except json.JSONDecodeError: continue
    return result


def _local_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError: return None


class TelemetryCollector:
    def __init__(self, *, db_path: str | Path, repo_path: str | Path | None = None,
                 provider_url: str | None = None, companion_bind: str | None = None):
        self.db_path = Path(db_path)
        self.repo_path = Path(repo_path or Path.cwd())
        self.provider_url = provider_url
        self.companion_bind = companion_bind
        self._cpu_sample: tuple[int, int] | None = None

    def collect(self, *, active_tasks: int, running_executions: int, provider_configured: bool) -> dict[str, object]:
        disk = shutil.disk_usage("/")
        return {"machine": {"cpu": {"utilization_percent": self._cpu_usage(), "load_average": list(os.getloadavg()), "temperature_c": self._cpu_temperature()},
                            "memory": _memory(), "root_disk": {"used_bytes": disk.used, "total_bytes": disk.total},
                            "models_bytes": _bytes(Path("/srv/atlas/models")), "atlas_db_bytes": _bytes(self.db_path)},
                "gpu": _gpu(), "atlas": {"healthy": True, "provider_configured": provider_configured,
                                              "provider_healthy": self._provider_healthy(),
                                              "active_tasks": active_tasks, "running_executions": running_executions},
                "docker": _docker(), "network": {"server_ip": _local_ip(), "companion_bind": self.companion_bind,
                                                      "inference_bind": self.provider_url}, "git": self._git()}

    def _provider_healthy(self) -> bool | None:
        if not self.provider_url: return None
        try:
            with urlopen(self.provider_url.rstrip("/") + "/health", timeout=2) as response:
                return 200 <= response.status < 300
        except (OSError, URLError): return False

    def _cpu_temperature(self) -> float | int | None:
        try:
            readings = [int(path.read_text().strip()) / 1000 for path in Path("/sys/class/thermal").glob("thermal_zone*/temp")]
            return round(max(readings), 1) if readings else None
        except (OSError, ValueError): return None

    def _cpu_usage(self) -> float | None:
        try:
            fields = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
            total, idle = sum(fields), fields[3] + (fields[4] if len(fields) > 4 else 0)
        except (OSError, ValueError, IndexError): return None
        previous, self._cpu_sample = self._cpu_sample, (total, idle)
        if previous is None or total == previous[0]: return None
        return round(100 * (1 - (idle - previous[1]) / (total - previous[0])), 1)

    def _git(self) -> dict[str, object]:
        branch = _command("git", "-C", str(self.repo_path), "branch", "--show-current")
        commit = _command("git", "-C", str(self.repo_path), "rev-parse", "--short", "HEAD")
        status = _command("git", "-C", str(self.repo_path), "status", "--porcelain")
        return {"branch": branch, "commit": commit, "worktree_clean": status == "" if status is not None else None}
