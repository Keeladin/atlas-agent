from __future__ import annotations

from pathlib import Path
from typing import Any
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .compose import ApiServices, DEFAULT_HOST, DEFAULT_PORT, compose_services
from .routes import advanced, auth_routes, chat, health, work
from .spa import CompanionStaticFiles


COMPANION_DIST = Path(__file__).resolve().parents[1] / "companion" / "dist"


class AppState:
    def __init__(self, services: ApiServices) -> None:
        self.services = services
        self.auth = services.auth


def create_app(
    *,
    services: ApiServices | None = None,
    serve_companion: bool = True,
    companion_dist: Path | None = None,
    **compose_kwargs: Any,
) -> Starlette:
    """ASGI application. Production bind target is 127.0.0.1:8080 behind Caddy."""

    api_services = services if services is not None else compose_services(**compose_kwargs)

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        try:
            yield
        finally:
            api_services.close()
    async def api_not_found(_request: Request) -> JSONResponse:
        return JSONResponse({"error": "not found"}, status_code=404)

    routes = [
        *health.routes,
        *auth_routes.routes,
        *chat.routes,
        *advanced.routes,
        *work.routes,
        # Keep unknown /api/* off the SPA fallback mount.
        Route(
            "/api/{rest:path}",
            api_not_found,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        ),
    ]
    dist = companion_dist if companion_dist is not None else COMPANION_DIST
    if serve_companion and dist.is_dir() and (dist / "index.html").is_file():
        routes.append(
            Mount(
                "/",
                app=CompanionStaticFiles(directory=str(dist), html=True),
                name="companion",
            )
        )

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=[],
                allow_credentials=True,
                allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
            )
        ],
    )
    app.state = AppState(api_services)  # type: ignore[attr-defined]
    return app


def default_listen() -> tuple[str, int]:
    return DEFAULT_HOST, DEFAULT_PORT
