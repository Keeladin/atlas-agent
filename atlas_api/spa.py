from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class CompanionStaticFiles(StaticFiles):
    """Serve built Companion assets with SPA history fallback.

    API routes are registered on the parent app and take precedence over this
    mount, so ``/api/*`` never falls through to ``index.html``.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
