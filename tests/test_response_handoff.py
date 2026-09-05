from __future__ import annotations

import asyncio

from atlas_api.handoff import ResponseHandoffMiddleware


def test_handoff_stamp_occurs_only_after_final_body_send_returns():
    events = []

    class Store:
        def mark_response_handed_off(self, turn_id):
            events.append(("stamp", turn_id))

    async def app(scope, receive, send):
        scope.setdefault("state", {})["handoff_owner_turn_id"] = "turn_owner"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def send(message):
        if message["type"] == "http.response.body":
            events.append(("body_send_return", None))

    middleware = ResponseHandoffMiddleware(app, chat_store=Store())
    asyncio.run(middleware({"type": "http", "state": {}}, lambda: None, send))
    assert events == [("body_send_return", None), ("stamp", "turn_owner")]


def test_handoff_stamp_failure_has_no_fallback_release(caplog):
    calls = []

    class Store:
        def mark_response_handed_off(self, turn_id):
            calls.append(turn_id)
            raise RuntimeError("database unavailable")

    async def app(scope, receive, send):
        scope.setdefault("state", {})["handoff_owner_turn_id"] = "turn_owner"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def send(_message):
        return None

    middleware = ResponseHandoffMiddleware(app, chat_store=Store())
    asyncio.run(middleware({"type": "http", "state": {}}, lambda: None, send))
    assert calls == ["turn_owner"]
    assert "response handoff stamp failed for owner turn turn_owner" in caplog.text
