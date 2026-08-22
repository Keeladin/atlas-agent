from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from atlas_api.auth import require_mutation_auth
from atlas_api.chat_handoff import (
    ChatHandoffError,
    intent_from_conversation,
    is_unknown_conversation,
)
from atlas_core.advanced import AdvancedError, TaskBrief, UnsupportedBrief


async def create_brief(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    payload_in = body or {}
    source = None
    conversation_id = str(payload_in.get("conversation_id") or "").strip()
    if conversation_id:
        until_turn_id = payload_in.get("until_turn_id")
        until = None if until_turn_id is None else str(until_turn_id).strip() or None
        revision = str(payload_in.get("revision") or "").strip() or None
        try:
            folded = intent_from_conversation(
                request.app.state.services.chat,
                conversation_id=conversation_id,
                until_turn_id=until,
                revision=revision,
            )
        except ChatHandoffError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ValueError as exc:
            if is_unknown_conversation(exc):
                return JSONResponse({"error": str(exc)}, status_code=404)
            return JSONResponse({"error": str(exc)}, status_code=400)
        objective = folded.objective
        notes_text = folded.notes
        source = folded.source()
    else:
        objective = str(payload_in.get("objective") or "")
        notes = payload_in.get("notes")
        notes_text = None if notes is None else str(notes)
    try:
        result = request.app.state.services.advanced.brief(
            objective, notes=notes_text
        )
    except (AdvancedError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if isinstance(result, UnsupportedBrief):
        payload = result.as_dict()
        if source is not None:
            payload["source"] = source
        return JSONResponse(payload)
    if not isinstance(result, TaskBrief):
        return JSONResponse({"error": "Advanced returned an invalid brief"}, status_code=500)
    if not result.capabilities:
        return JSONResponse(
            {"error": "TaskBrief requires at least one capability id"},
            status_code=500,
        )
    payload = result.as_dict()
    payload["status"] = "brief"
    if source is not None:
        payload["source"] = source
    return JSONResponse(payload)


routes = [
    Route("/api/advanced/brief", create_brief, methods=["POST"]),
]
