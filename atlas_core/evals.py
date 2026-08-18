from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    name: str
    input: Any
    success_criteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalAttempt:
    case: str
    attempt: int
    passed: bool
    detail: str


@dataclass(frozen=True)
class EvalReport:
    capability_id: str
    attempts: tuple[EvalAttempt, ...]
    pass_at_1: float
    pass_at_k: float
    pass_all_k: float
    k: int


Runner = Callable[[EvalCase], Any]
Grader = Callable[[EvalCase, Any], tuple[bool, str]]


class EvalHarness:
    """Small eval-driven-development harness for capability reliability."""

    def run(self, capability_id: str, cases: tuple[EvalCase, ...], *, runner: Runner, grader: Grader, k: int = 3) -> EvalReport:
        if k < 1:
            raise ValueError("k must be >= 1")
        attempts: list[EvalAttempt] = []
        first_passes = 0
        any_passes = 0
        all_passes = 0
        for case in cases:
            results: list[bool] = []
            for attempt in range(1, k + 1):
                try:
                    output = runner(case)
                    passed, detail = grader(case, output)
                except Exception as exc:
                    passed, detail = False, f"runner/grader error: {exc}"
                results.append(bool(passed))
                attempts.append(EvalAttempt(case.name, attempt, bool(passed), str(detail)))
            first_passes += int(results[0])
            any_passes += int(any(results))
            all_passes += int(all(results))
        denominator = len(cases) or 1
        return EvalReport(
            capability_id=capability_id,
            attempts=tuple(attempts),
            pass_at_1=first_passes / denominator,
            pass_at_k=any_passes / denominator,
            pass_all_k=all_passes / denominator,
            k=k,
        )
