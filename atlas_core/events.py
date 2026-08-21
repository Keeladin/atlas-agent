from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeEvent:
    name: str
    work_id: str
    step_id: str | None = None
    execution_id: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class EventHandlerError:
    event_name: str
    handler_name: str
    error: str


EventHandler = Callable[[RuntimeEvent], None]


class EventBus:
    """Small in-process fan-out bus for non-authoritative observers.

    Durable event truth is written by WorkStore before fan-out. Observer bugs
    must never become orchestration failures, so handler exceptions are isolated
    and retained for diagnostics.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._all_handlers: list[EventHandler] = []
        self._errors: list[EventHandlerError] = []

    def subscribe(self, name: str, handler: EventHandler) -> None:
        if name == "*":
            self._all_handlers.append(handler)
        else:
            self._handlers[name].append(handler)

    def emit(self, event: RuntimeEvent) -> None:
        handlers = [
            *tuple(self._handlers.get(event.name, ())),
            *tuple(self._all_handlers),
        ]
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                self._errors.append(
                    EventHandlerError(
                        event_name=event.name,
                        handler_name=getattr(handler, "__name__", type(handler).__name__),
                        error=str(exc),
                    )
                )

    def errors(self) -> tuple[EventHandlerError, ...]:
        return tuple(self._errors)
