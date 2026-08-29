from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount, Route

from atlas_api.auth import auth_from_env
from atlas_api.compose import build_runtime
from atlas_api.routes import api
from atlas_api.spa import CompanionStaticFiles


async def _cadence_loop(app: Starlette) -> None:
    while True:
        try:
            await asyncio.to_thread(app.state.runtime.cadence.tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(30)


def create_app(*, instance_root: str | Path = "instance", static_dir: str | Path | None = None) -> Starlette:
    runtime = build_runtime(instance_root)
    auth = auth_from_env(env_file=Path(instance_root) / "companion-auth.env")
    auth.identities = runtime.identities

    @asynccontextmanager
    async def lifespan(app: Starlette):
        task = asyncio.create_task(_cadence_loop(app), name="atlas-cadence")
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            if runtime.sources.registry is not None:
                runtime.sources.registry.close()

    routes = [
        Route("/api/health", api.health, methods=["GET"]),
        Route("/api/auth/session", api.auth_session, methods=["GET"]),
        Route("/api/auth/login", api.auth_login, methods=["POST"]),
        Route("/api/auth/logout", api.auth_logout, methods=["POST"]),
        Route("/api/system", api.system_state, methods=["GET"]),
        Route("/api/capabilities", api.capabilities_list, methods=["GET"]),
        Route("/api/capabilities/search", api.capabilities_search, methods=["GET"]),
        Route("/api/capabilities/{capability_id:str}/invoke", api.capability_invoke, methods=["POST"]),
        Route("/api/policy", api.policy_list, methods=["GET"]),
        Route("/api/policy/history", api.policy_history, methods=["GET"]),
        Route("/api/policy", api.policy_set, methods=["POST"]),
        Route("/api/actions/pending", api.actions_pending, methods=["GET"]),
        Route("/api/actions/{occurrence_id:str}/confirm", api.action_confirm, methods=["POST"]),
        Route("/api/actions/{occurrence_id:str}/cancel", api.action_cancel, methods=["POST"]),
        Route("/api/chat/conversations", api.conversations_list, methods=["GET"]),
        Route("/api/chat/conversations", api.conversation_create, methods=["POST"]),
        Route("/api/chat/conversations/{conversation_id:str}", api.conversation_detail, methods=["GET"]),
        Route("/api/chat/conversations/{conversation_id:str}/messages", api.conversation_send, methods=["POST"]),
        Route("/api/work", api.work_list, methods=["GET"]),
        Route("/api/work", api.work_create, methods=["POST"]),
        Route("/api/work/{work_id:str}", api.work_detail, methods=["GET"]),
        Route("/api/work/{work_id:str}/{action:str}", api.work_action, methods=["POST"]),
        Route("/api/cadence", api.cadence_list, methods=["GET"]),
        Route("/api/cadence", api.cadence_create, methods=["POST"]),
        Route("/api/cadence/{cadence_id:str}/enabled", api.cadence_enable, methods=["POST"]),
        Route("/api/cadence/{cadence_id:str}", api.cadence_delete, methods=["DELETE"]),
        Route("/api/sources/roots", api.source_roots_list, methods=["GET"]),
        Route("/api/sources/roots", api.source_root_put, methods=["POST"]),
        Route("/api/sources/roots/{root_id:str}", api.source_root_delete, methods=["DELETE"]),
        Route("/api/knowledge", api.knowledge_list, methods=["GET"]),
        Route("/api/knowledge/{item_id:str}", api.knowledge_delete, methods=["DELETE"]),
        Route("/api/mcp", api.mcp_list, methods=["GET"]),
        Route("/api/mcp", api.mcp_put, methods=["POST"]),
        Route("/api/mcp/{server_id:str}/refresh", api.mcp_refresh, methods=["POST"]),
        Route("/api/mcp/{server_id:str}", api.mcp_delete, methods=["DELETE"]),
        Route("/api/providers", api.providers_list, methods=["GET"]),
        Route("/api/providers", api.provider_put, methods=["POST"]),
        Route("/api/providers/{provider_key:str}/verify", api.provider_verify, methods=["POST"]),
        Route("/api/providers/{provider_key:str}", api.provider_delete, methods=["DELETE"]),
        Route("/api/connections", api.connections_list, methods=["GET"]),
        Route("/api/mail/attest", api.mail_attest, methods=["POST"]),
    ]
    resolved_static = (
        Path(static_dir)
        if static_dir is not None
        else Path(__file__).parents[1] / "companion" / "dist"
    )
    if resolved_static.is_dir():
        routes.append(
            Mount(
                "/",
                app=CompanionStaticFiles(directory=str(resolved_static), html=True),
                name="companion",
            )
        )
    app = Starlette(debug=False, routes=routes, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.auth = auth
    return app
