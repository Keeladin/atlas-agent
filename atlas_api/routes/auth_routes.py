from __future__ import annotations

import json
import math

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from atlas_api.auth import AuthService, client_key, unauthorized


async def login(request: Request) -> JSONResponse:
    auth: AuthService = request.app.state.auth
    key = client_key(request)
    decision = auth.login_throttle.check(key)
    if not decision.allowed:
        retry = max(1, int(math.ceil(decision.retry_after_seconds)))
        return JSONResponse(
            {
                "error": "too many failed login attempts",
                "retry_after_seconds": retry,
                "failures": decision.failures,
            },
            status_code=429,
            headers={"Retry-After": str(retry)},
        )
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    password = str((body or {}).get("password") or "")
    if not auth.verify_password(password):
        failed = auth.login_throttle.record_failure(key)
        if not failed.allowed:
            retry = max(1, int(math.ceil(failed.retry_after_seconds)))
            return JSONResponse(
                {
                    "error": "too many failed login attempts",
                    "retry_after_seconds": retry,
                    "failures": failed.failures,
                },
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        return unauthorized("invalid credentials")
    auth.login_throttle.clear(key)
    session = auth.issue_session()
    response = JSONResponse(
        {
            "authenticated": True,
            "subject": session.subject,
            "csrf_token": session.csrf_token,
            "cookie_policy": auth.cookie_policy.as_dict(),
        }
    )
    auth.set_session_cookie(response, session)
    return response


async def logout(request: Request) -> JSONResponse:
    auth: AuthService = request.app.state.auth
    response = JSONResponse({"authenticated": False})
    auth.clear_session_cookie(response)
    return response


async def session(request: Request) -> JSONResponse:
    auth: AuthService = request.app.state.auth
    current = auth.session_from_request(request)
    if current is None:
        return JSONResponse(
            {
                "authenticated": False,
                "cookie_policy": auth.cookie_policy.as_dict(),
            }
        )
    return JSONResponse(
        {
            "authenticated": True,
            "subject": current.subject,
            "csrf_token": current.csrf_token,
            "cookie_policy": auth.cookie_policy.as_dict(),
        }
    )


routes = [
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/logout", logout, methods=["POST"]),
    Route("/api/auth/session", session, methods=["GET"]),
]
