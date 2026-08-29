from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    retry_after_seconds: float = 0.0
    failures: int = 0


class LoginThrottle:
    """Bounded in-process backoff for the single-user login endpoint.

    Tracks failures by client key (usually remote IP). Successful login clears
    the key. Delays grow exponentially and cap at ``max_delay_seconds``.
    """

    def __init__(
        self,
        *,
        max_failures_before_delay: int = 3,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 60.0,
        window_seconds: float = 900.0,
    ) -> None:
        if max_failures_before_delay < 1:
            raise ValueError("max_failures_before_delay must be >= 1")
        if base_delay_seconds <= 0 or max_delay_seconds <= 0:
            raise ValueError("delay values must be positive")
        self._max_failures_before_delay = max_failures_before_delay
        self._base_delay = base_delay_seconds
        self._max_delay = max_delay_seconds
        self._window = window_seconds
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def check(self, key: str) -> ThrottleDecision:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            until = self._blocked_until.get(key)
            if until is not None and until > now:
                return ThrottleDecision(
                    allowed=False,
                    retry_after_seconds=max(0.0, until - now),
                    failures=len(self._failures.get(key, ())),
                )
            return ThrottleDecision(
                allowed=True,
                failures=len(self._failures.get(key, ())),
            )

    def record_failure(self, key: str) -> ThrottleDecision:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            stamps = self._failures.setdefault(key, [])
            stamps.append(now)
            failures = len(stamps)
            # Current attempt still completes as a normal auth failure. Backoff
            # applies to subsequent requests once the threshold is reached.
            if failures >= self._max_failures_before_delay:
                exponent = max(0, failures - self._max_failures_before_delay)
                delay = min(self._max_delay, self._base_delay * math.pow(2, exponent))
                self._blocked_until[key] = now + delay
            return ThrottleDecision(allowed=True, failures=failures)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)

    def _prune(self, key: str, now: float) -> None:
        stamps = self._failures.get(key)
        if stamps:
            kept = [stamp for stamp in stamps if now - stamp <= self._window]
            if kept:
                self._failures[key] = kept
            else:
                self._failures.pop(key, None)
        until = self._blocked_until.get(key)
        if until is not None and until <= now:
            self._blocked_until.pop(key, None)
