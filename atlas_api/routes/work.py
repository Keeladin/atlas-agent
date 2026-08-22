from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from atlas_api.auth import require_mutation_auth, require_session
from atlas_api.sse import work_events_stream
from atlas_api.views.work import build_work_detail, work_list_item
from atlas_core.advanced.brief import TaskBrief
from atlas_core.work import WorkError, WorkStoreError
from atlas_core.work.store import InvalidTransitionError, UnknownRecordError


def _services(request: Request):
    return request.app.state.services


def _brief_from_body(payload: dict[str, Any]) -> TaskBrief:
    brief_payload = payload.get("brief")
    if not isinstance(brief_payload, dict):
        raise ValueError("brief object is required")
    if brief_payload.get("status") == "unsupported":
        raise ValueError("unsupported brief cannot become Work")
    capabilities = brief_payload.get("capabilities") or ()
    if isinstance(capabilities, str):
        capabilities = (capabilities,)
    constraints = brief_payload.get("constraints") or ()
    if isinstance(constraints, str):
        constraints = (constraints,)
    return TaskBrief(
        objective=str(brief_payload.get("objective") or ""),
        capabilities=tuple(str(item) for item in capabilities),
        required_authority=str(brief_payload.get("required_authority") or "read"),
        expected_effect=str(brief_payload.get("expected_effect") or ""),
        constraints=tuple(str(item) for item in constraints),
        deliverable_kind=brief_payload.get("deliverable_kind"),
        notes=brief_payload.get("notes"),
    )


async def list_work(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    status = request.query_params.get("status")
    items = _services(request).work.store.list_work(status=status)
    return JSONResponse({"work": [work_list_item(item) for item in items]})


async def create_work(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    try:
        brief = _brief_from_body(body or {})
        authority_scope = str((body or {}).get("authority_scope") or brief.required_authority)
        inputs = (body or {}).get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            raise ValueError("inputs must be an object")
        work_id = _services(request).work.accept(
            brief, authority_scope, inputs=inputs
        )
    except (WorkError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    detail = build_work_detail(_services(request).work, work_id)
    return JSONResponse(detail.as_dict(), status_code=201)


async def get_work(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    work_id = request.path_params["work_id"]
    try:
        record = _services(request).work.get(work_id)
    except (WorkError, UnknownRecordError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse(
        {
            "work_id": record.id,
            "objective": record.objective,
            "status": record.status,
            "authority_scope": record.authority_scope,
            "capabilities": list(record.capabilities),
        }
    )


async def get_contract(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    work_id = request.path_params["work_id"]
    try:
        contract = _services(request).work.contract(work_id)
    except (WorkError, UnknownRecordError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    payload = contract.as_payload()
    payload["contract_id"] = contract.contract_id
    payload["sha256"] = contract.sha256
    return JSONResponse(payload)


async def get_detail(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    work_id = request.path_params["work_id"]
    try:
        detail = build_work_detail(_services(request).work, work_id)
    except (WorkError, UnknownRecordError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse(detail.as_dict())


async def run_work(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    work_id = request.path_params["work_id"]
    runtime = _services(request).work
    try:
        resumed = runtime.resume(work_id)
        result = runtime.run(work_id)
    except (WorkError, UnknownRecordError, InvalidTransitionError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    detail = build_work_detail(runtime, work_id)
    return JSONResponse(
        {
            "resumed": resumed,
            "result": {
                "work_id": result.work_id,
                "status": result.status,
                "cycles": result.cycles,
                "executions": result.executions,
                "reason": result.reason,
            },
            "detail": detail.as_dict(),
        }
    )


async def recover_work(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    work_id = request.path_params["work_id"]
    runtime = _services(request).work
    try:
        result = runtime.recover(work_id)
    except (WorkError, UnknownRecordError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    detail = build_work_detail(runtime, work_id)
    return JSONResponse(
        {
            "result": {
                "work_id": result.work_id,
                "recovered": result.recovered,
                "failed_closed": result.failed_closed,
                "status": result.status,
            },
            "detail": detail.as_dict(),
        }
    )


async def cancel_work(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    work_id = request.path_params["work_id"]
    store = _services(request).work.store
    try:
        current = store.get_work(work_id)
        if current.status in {"completed", "failed", "cancelled"}:
            return JSONResponse(
                {"error": f"work is already {current.status}"},
                status_code=400,
            )
        store.set_work_status(work_id, "cancelled", force=True)
    except (UnknownRecordError, WorkStoreError, InvalidTransitionError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    detail = build_work_detail(_services(request).work, work_id)
    return JSONResponse(detail.as_dict())


async def approve_authority(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    approval_id = request.path_params["approval_id"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    note = (body or {}).get("note")
    try:
        record = _services(request).work.approve(
            approval_id, note=None if note is None else str(note)
        )
    except (UnknownRecordError, InvalidTransitionError, ValueError) as exc:
        status = 404 if isinstance(exc, UnknownRecordError) else 400
        return JSONResponse({"error": str(exc)}, status_code=status)
    return JSONResponse(
        {
            "id": record.id,
            "work_id": record.work_id,
            "status": record.status,
            "decision_note": record.decision_note,
            "kind": "authority_approval",
        }
    )


async def deny_authority(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    approval_id = request.path_params["approval_id"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    note = (body or {}).get("note")
    try:
        record = _services(request).work.deny(
            approval_id, note=None if note is None else str(note)
        )
    except (UnknownRecordError, InvalidTransitionError, ValueError) as exc:
        status = 404 if isinstance(exc, UnknownRecordError) else 400
        return JSONResponse({"error": str(exc)}, status_code=status)
    return JSONResponse(
        {
            "id": record.id,
            "work_id": record.work_id,
            "status": record.status,
            "decision_note": record.decision_note,
            "kind": "authority_approval",
        }
    )


async def confirm_payload(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    confirmation_id = request.path_params["confirmation_id"]
    try:
        record = _services(request).work.confirm_payload(confirmation_id)
    except (UnknownRecordError, InvalidTransitionError, ValueError) as exc:
        status = 404 if isinstance(exc, UnknownRecordError) else 400
        return JSONResponse({"error": str(exc)}, status_code=status)
    return JSONResponse(
        {
            "id": record.id,
            "work_id": record.work_id,
            "status": record.status,
            "summary": record.summary,
            "payload_sha256": record.payload_sha256,
            "kind": "payload_confirmation",
        }
    )


async def deny_confirmation(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    confirmation_id = request.path_params["confirmation_id"]
    try:
        record = _services(request).work.deny_confirmation(confirmation_id)
    except (UnknownRecordError, InvalidTransitionError, ValueError) as exc:
        status = 404 if isinstance(exc, UnknownRecordError) else 400
        return JSONResponse({"error": str(exc)}, status_code=status)
    return JSONResponse(
        {
            "id": record.id,
            "work_id": record.work_id,
            "status": record.status,
            "summary": record.summary,
            "payload_sha256": record.payload_sha256,
            "kind": "payload_confirmation",
        }
    )


async def cancel_confirmation(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    confirmation_id = request.path_params["confirmation_id"]
    try:
        record = _services(request).work.cancel_confirmation(confirmation_id)
    except (UnknownRecordError, InvalidTransitionError, ValueError) as exc:
        status = 404 if isinstance(exc, UnknownRecordError) else 400
        return JSONResponse({"error": str(exc)}, status_code=status)
    return JSONResponse(
        {
            "id": record.id,
            "work_id": record.work_id,
            "status": record.status,
            "summary": record.summary,
            "payload_sha256": record.payload_sha256,
            "kind": "payload_confirmation",
        }
    )


routes = [
    Route("/api/work", list_work, methods=["GET"]),
    Route("/api/work", create_work, methods=["POST"]),
    Route("/api/work/{work_id}", get_work, methods=["GET"]),
    Route("/api/work/{work_id}/contract", get_contract, methods=["GET"]),
    Route("/api/work/{work_id}/detail", get_detail, methods=["GET"]),
    Route("/api/work/{work_id}/run", run_work, methods=["POST"]),
    Route("/api/work/{work_id}/recover", recover_work, methods=["POST"]),
    Route("/api/work/{work_id}/cancel", cancel_work, methods=["POST"]),
    Route("/api/work/{work_id}/events/stream", work_events_stream, methods=["GET"]),
    Route(
        "/api/work/approvals/{approval_id}/approve",
        approve_authority,
        methods=["POST"],
    ),
    Route(
        "/api/work/approvals/{approval_id}/deny",
        deny_authority,
        methods=["POST"],
    ),
    Route(
        "/api/work/confirmations/{confirmation_id}/confirm",
        confirm_payload,
        methods=["POST"],
    ),
    Route(
        "/api/work/confirmations/{confirmation_id}/deny",
        deny_confirmation,
        methods=["POST"],
    ),
    Route(
        "/api/work/confirmations/{confirmation_id}/cancel",
        cancel_confirmation,
        methods=["POST"],
    ),
]
