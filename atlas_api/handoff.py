from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class ResponseHandoffMiddleware:
    """Stamp owner response handoff only after the final body send returns."""

    def __init__(self, app, *, chat_store) -> None:
        self.app = app
        self.chat_store = chat_store

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        stamped = False

        async def send_with_handoff(message):
            nonlocal stamped
            await send(message)
            if stamped or message.get("type") != "http.response.body" or message.get("more_body", False):
                return
            owner_turn_id = str((scope.get("state") or {}).get("handoff_owner_turn_id") or "")
            if not owner_turn_id:
                return
            try:
                await asyncio.to_thread(self.chat_store.mark_response_handed_off, owner_turn_id)
                stamped = True
            except Exception:
                logger.critical(
                    "response handoff stamp failed for owner turn %s",
                    owner_turn_id,
                    exc_info=True,
                )

        await self.app(scope, receive, send_with_handoff)
