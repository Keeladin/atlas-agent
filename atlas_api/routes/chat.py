from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from atlas_api.auth import require_mutation_auth, require_session
from atlas_core.chat import ChatError


def _services(request: Request):
    return request.app.state.services


async def list_conversations(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    archived = request.query_params.get("archived") in {"1", "true", "yes"}
    items = _services(request).chat.list_conversations(archived=archived)
    return JSONResponse({"conversations": [item.as_dict() for item in items]})


async def create_conversation(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    title = str((body or {}).get("title") or "Chat")
    view = _services(request).chat.create_conversation(title=title)
    return JSONResponse(view.as_dict(), status_code=201)


async def get_conversation(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    conversation_id = request.path_params["conversation_id"]
    try:
        view = _services(request).chat.conversation(conversation_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse(view.as_dict())


async def update_conversation(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    conversation_id = request.path_params["conversation_id"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    chat = _services(request).chat
    payload = body or {}
    try:
        if "title" in payload:
            chat.rename_conversation(conversation_id, str(payload.get("title") or ""))
        if "pinned" in payload:
            chat.pin_conversation(conversation_id, bool(payload.get("pinned")))
        if "archived" in payload:
            chat.archive_conversation(conversation_id, bool(payload.get("archived")))
        if not any(key in payload for key in ("title", "pinned", "archived")):
            return JSONResponse({"error": "no conversation update provided"}, status_code=400)
        view = chat.conversation(conversation_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse(view.as_dict())


async def delete_conversation(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    conversation_id = request.path_params["conversation_id"]
    try:
        _services(request).chat.delete_conversation(conversation_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse({"deleted": conversation_id})


async def send_message(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    conversation_id = request.path_params.get("conversation_id")
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    message = str((body or {}).get("message") or "")
    cid = conversation_id or (body or {}).get("conversation_id")
    try:
        reply = _services(request).chat.respond(message, conversation_id=cid)
    except (ChatError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "conversation_id": reply.conversation_id,
            "reply": reply.reply,
            "conversation": reply.conversation.as_dict(),
        }
    )


routes = [
    Route("/api/chat/conversations", list_conversations, methods=["GET"]),
    Route("/api/chat/conversations", create_conversation, methods=["POST"]),
    Route(
        "/api/chat/conversations/{conversation_id}",
        get_conversation,
        methods=["GET"],
    ),
    Route(
        "/api/chat/conversations/{conversation_id}",
        update_conversation,
        methods=["PATCH"],
    ),
    Route(
        "/api/chat/conversations/{conversation_id}",
        delete_conversation,
        methods=["DELETE"],
    ),
    Route(
        "/api/chat/conversations/{conversation_id}/messages",
        send_message,
        methods=["POST"],
    ),
    Route("/api/chat/messages", send_message, methods=["POST"]),
]
