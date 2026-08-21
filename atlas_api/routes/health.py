from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from atlas_api.compose import DEFAULT_HOST, DEFAULT_PORT


async def health(request: Request) -> JSONResponse:
    services = request.app.state.services
    return JSONResponse(
        {
            "ok": True,
            "service": "atlas_api",
            "listen": {"host": services.host, "port": services.port},
            "defaults": {"host": DEFAULT_HOST, "port": DEFAULT_PORT},
            "runtimes": ["chat", "advanced", "work"],
        }
    )


routes = [
    Route("/api/health", health, methods=["GET"]),
]
