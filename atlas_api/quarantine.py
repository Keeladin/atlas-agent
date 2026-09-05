from __future__ import annotations

from starlette.responses import JSONResponse


_ALLOWED_PREFIXES = (
    "/api/health",
    "/api/auth/session",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/quarantine",
)


class QuarantineMiddleware:
    """Keep diagnostics/repair reachable while refusing normal Atlas traffic."""

    def __init__(self, app, *, operational) -> None:
        self.app = app
        self.operational = operational

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        state = self.operational.state()
        if not bool(state.get("quarantined")):
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        if any(path == prefix or path.startswith(prefix + "/") for prefix in _ALLOWED_PREFIXES):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {
                "error": "Atlas is quarantined because persisted runtime invariants are invalid",
                "code": "runtime_quarantined",
                "active_event_id": state.get("active_event_id"),
            },
            status_code=503,
        )
        await response(scope, receive, send)
