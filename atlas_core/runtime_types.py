from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class RuntimeBudget:
    max_executions: int = 1000
    max_cycles: int = 1000
    max_model_calls: int = 200
    max_parallel_workers: int = 4
    max_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.max_executions < 1:
            raise ValueError("max_executions must be >= 1")
        if self.max_cycles < 1:
            raise ValueError("max_cycles must be >= 1")
        if self.max_model_calls < 0:
            raise ValueError("max_model_calls must be >= 0")
        if self.max_parallel_workers < 1:
            raise ValueError("max_parallel_workers must be >= 1")
        if self.max_cost_usd is not None and self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be >= 0")

@dataclass(frozen=True)
class RuntimeResult:
    task_id: str
    status: str
    cycles: int
    executions: int
    reason: str

@dataclass(frozen=True)
class RecoveryResult:
    task_id: str
    recovered: int
    failed_closed: int
    status: str
