from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WebResponse:
    requested_url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    fetched_at: str
    provider: str


@dataclass(frozen=True)
class RenderedPage:
    requested_url: str
    final_url: str
    title: str
    visible_text: str
    links: tuple[dict[str, str], ...]
    rendered_at: str
    provider: str
    dom_sha256: str
    resource_count: int
    resource_bytes: int


class BrowserProvider(Protocol):
    """Read-only rendered browser below Atlas's governed render capability."""

    provider_id: str

    def availability(self) -> tuple[bool, str]: ...

    def render(self, url: str, *, timeout_ms: int, settle_ms: int, max_chars: int) -> RenderedPage: ...


class WebProvider(Protocol):
    """Replaceable transport below Atlas's stable, governed web capabilities."""

    provider_id: str

    def availability(self) -> tuple[bool, str]: ...

    def search_availability(self) -> tuple[bool, str]: ...

    def search(self, query: str, *, limit: int) -> list[dict[str, object]]: ...

    def fetch(self, url: str, *, max_bytes: int) -> WebResponse: ...
