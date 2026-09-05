from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount, Route

from atlas_api.auth import auth_from_env
from atlas_api.handoff import ResponseHandoffMiddleware
from atlas_api.compose import build_runtime
from atlas_api.routes import api
from atlas_api.spa import CompanionStaticFiles

logger = logging.getLogger(__name__)


async def _cadence_loop(app: Starlette) -> None:
    while True:
        try:
            await asyncio.to_thread(app.state.runtime.cadence.tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("cadence tick failed", exc_info=True)
        await asyncio.sleep(30)


async def _work_loop(app: Starlette) -> None:
    """Detached execution owner for ledger-aware Work."""
    await asyncio.sleep(0.1)
    while True:
        try:
            await asyncio.to_thread(app.state.runtime.work.promote_runnable)
            await asyncio.to_thread(app.state.runtime.work.run_runnable)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("detached Work loop failed", exc_info=True)
        await asyncio.sleep(1)


async def _obligation_loop(app: Starlette) -> None:
    """Reconcile obligation truth from durable Action/Evidence and verified reports."""
    await asyncio.sleep(0.5)
    while True:
        try:
            await asyncio.to_thread(app.state.runtime.obligation_reconciler.tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("obligation reconciliation failed", exc_info=True)
        await asyncio.sleep(2)


def create_app(*, instance_root: str | Path = "instance", static_dir: str | Path | None = None) -> Starlette:
    runtime = build_runtime(instance_root)
    auth = auth_from_env(env_file=Path(instance_root) / "companion-auth.env")
    auth.identities = runtime.identities

    @asynccontextmanager
    async def lifespan(app: Starlette):
        cadence_task = asyncio.create_task(_cadence_loop(app), name="atlas-cadence")
        work_task = asyncio.create_task(_work_loop(app), name="atlas-work")
        obligation_task = asyncio.create_task(_obligation_loop(app), name="atlas-obligations")
        try:
            yield
        finally:
            for task in (cadence_task, work_task, obligation_task):
                task.cancel()
            for task in (cadence_task, work_task, obligation_task):
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
        Route("/api/attention", api.attention_snapshot, methods=["GET"]),
        Route("/api/capabilities", api.capabilities_list, methods=["GET"]),
        Route("/api/capabilities/search", api.capabilities_search, methods=["GET"]),
        Route("/api/capabilities/{capability_id:str}/invoke", api.capability_invoke, methods=["POST"]),
        Route("/api/policy", api.policy_list, methods=["GET"]),
        Route("/api/policy/history", api.policy_history, methods=["GET"]),
        Route("/api/policy", api.policy_set, methods=["POST"]),
        Route("/api/chat/conversations", api.conversations_list, methods=["GET"]),
        Route("/api/chat/conversations", api.conversation_create, methods=["POST"]),
        Route("/api/chat/conversations/{conversation_id:str}", api.conversation_detail, methods=["GET"]),
        Route("/api/chat/conversations/{conversation_id:str}", api.conversation_delete, methods=["DELETE"]),
        Route("/api/chat/conversations/{conversation_id:str}/messages", api.conversation_send, methods=["POST"]),
        Route("/api/chat/attachments", api.chat_attachment_upload, methods=["POST"]),
        Route("/api/work", api.work_list, methods=["GET"]),
        Route("/api/work", api.work_create, methods=["POST"]),
        Route("/api/work/{work_id:str}", api.work_detail, methods=["GET"]),
        Route("/api/work/{work_id:str}/{action:str}", api.work_action, methods=["POST"]),
        Route("/api/cadence", api.cadence_list, methods=["GET"]),
        Route("/api/cadence", api.cadence_create, methods=["POST"]),
        Route("/api/cadence/{cadence_id:str}/enabled", api.cadence_enable, methods=["POST"]),
        Route("/api/cadence/{cadence_id:str}", api.cadence_delete, methods=["DELETE"]),
        Route("/api/sources/roots", api.source_roots_list, methods=["GET"]),
        Route("/api/sources/file", api.source_file_view, methods=["GET"]),
        Route("/api/sources/roots", api.source_root_put, methods=["POST"]),
        Route("/api/sources/roots/{root_id:str}", api.source_root_delete, methods=["DELETE"]),
        Route("/api/library/scans", api.library_scans, methods=["GET"]),
        Route("/api/library/scans/{scan_id:str}", api.library_scan_detail, methods=["GET"]),
        Route("/api/library/reviews", api.library_reviews, methods=["GET"]),
        Route("/api/knowledge", api.knowledge_list, methods=["GET"]),
        Route("/api/knowledge/promote", api.knowledge_promote, methods=["POST"]),
        Route("/api/knowledge/{item_id:str}", api.knowledge_delete, methods=["DELETE"]),
        Route("/api/knowledge/generations", api.knowledge_generations, methods=["GET"]),
        Route("/api/artifacts", api.artifacts_list, methods=["GET"]),
        Route("/api/artifacts/{artifact_id:str}", api.artifact_detail, methods=["GET"]),
        Route("/api/artifacts/{artifact_id:str}/content", api.artifact_content, methods=["GET"]),
        Route("/api/memory", api.memory_list, methods=["GET"]),
        Route("/api/memory/{item_id:str}", api.memory_detail, methods=["GET"]),
        Route("/api/memory/{item_id:str}/{action:str}", api.memory_action, methods=["POST"]),
        Route("/api/mcp", api.mcp_list, methods=["GET"]),
        Route("/api/mcp", api.mcp_put, methods=["POST"]),
        Route("/api/mcp/{server_id:str}/refresh", api.mcp_refresh, methods=["POST"]),
        Route("/api/mcp/{server_id:str}", api.mcp_delete, methods=["DELETE"]),
        Route("/api/providers", api.providers_list, methods=["GET"]),
        Route("/api/providers", api.provider_put, methods=["POST"]),
        Route("/api/providers/{provider_key:str}/verify", api.provider_verify, methods=["POST"]),
        Route("/api/providers/{provider_key:str}", api.provider_delete, methods=["DELETE"]),
        Route("/api/web/providers", api.web_providers_list, methods=["GET"]),
        Route("/api/web/providers", api.web_provider_put, methods=["POST"]),
        Route("/api/web/providers/{provider_key:str}/verify", api.web_provider_verify, methods=["POST"]),
        Route("/api/web/providers/{provider_key:str}", api.web_provider_delete, methods=["DELETE"]),
        Route("/api/connections", api.connections_list, methods=["GET"]),
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
    app = Starlette(debug=False, routes=routes, lifespan=lifespan, middleware=[
        Middleware(ResponseHandoffMiddleware, chat_store=runtime.chat_store),
    ])
    app.state.runtime = runtime
    app.state.auth = auth
    return app
