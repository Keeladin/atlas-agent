from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from atlas_api.auth import require_mutation_auth
from atlas_core.advanced import AdvancedError


async def create_brief(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    objective = str((body or {}).get("objective") or "")
    notes = (body or {}).get("notes")
    notes_text = None if notes is None else str(notes)
    try:
        brief = request.app.state.services.advanced.brief(
            objective, notes=notes_text
        )
    except (AdvancedError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(brief.as_dict())


routes = [
    Route("/api/advanced/brief", create_brief, methods=["POST"]),
]
