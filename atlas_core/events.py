from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeEvent:
    name: str
    task_id: str
    step_id: str | None = None
    execution_id: str | None = None
    payload: dict[str, Any] | None = None


EventHandler = Callable[[RuntimeEvent], None]


class EventBus:
    """Small in-process fan-out bus; durable event truth remains in TaskStore."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._all_handlers: list[EventHandler] = []

    def subscribe(self, name: str, handler: EventHandler) -> None:
        if name == "*":
            self._all_handlers.append(handler)
        else:
            self._handlers[name].append(handler)

    def emit(self, event: RuntimeEvent) -> None:
        for handler in tuple(self._handlers.get(event.name, ())):
            handler(event)
        for handler in tuple(self._all_handlers):
            handler(event)
