from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse

from atlas_api.auth import require_session


async def work_events_stream(request: Request) -> EventSourceResponse | JSONResponse:
    """SSE over durable work_events. Cursor is the numeric event id."""

    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    work_id = request.path_params["work_id"]
    store = request.app.state.services.work.store
    try:
        store.get_work(work_id)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    after_raw = request.query_params.get("after") or "0"
    try:
        after = int(after_raw)
    except ValueError:
        return JSONResponse(
            {"error": "after cursor must be an integer event id"},
            status_code=400,
        )

    async def event_generator() -> AsyncIterator[dict]:
        cursor = after
        idle_rounds = 0
        while True:
            if await request.is_disconnected():
                break
            rows = [
                item
                for item in store.list_events(work_id)
                if int(item.id) > cursor
            ]
            if rows:
                idle_rounds = 0
                for item in rows:
                    cursor = int(item.id)
                    payload = {
                        "id": item.id,
                        "name": item.name,
                        "work_id": item.work_id,
                        "step_id": item.step_id,
                        "execution_id": item.execution_id,
                        "payload": item.payload,
                        "created_at": item.created_at,
                    }
                    yield {
                        "id": str(item.id),
                        "event": item.name,
                        "data": json.dumps(payload, ensure_ascii=False),
                    }
            else:
                idle_rounds += 1
                if idle_rounds % 15 == 0:
                    yield {
                        "event": "ping",
                        "data": json.dumps({"after": cursor}),
                    }
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())
