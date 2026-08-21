from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_core.advanced import AdvancedRuntime, build_advanced_runtime
from atlas_core.capabilities import CapabilityBinding, CapabilityOutcome
from atlas_core.chat import ChatRuntime, build_chat_runtime
from atlas_core.knowledge import KnowledgeStore, register_knowledge_capabilities
from atlas_core.verification import VerifierRegistry
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    WorkRuntime,
    build_work_runtime,
)

from .auth import AuthService, auth_from_env


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


@dataclass
class ApiServices:
    """Runtime handles owned by the API composition root."""

    chat: ChatRuntime
    advanced: AdvancedRuntime
    work: WorkRuntime
    auth: AuthService
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


def _local_email_recorder(request) -> CapabilityOutcome:
    """Deployment-local email recorder so confirmation-required Work can execute.

    Not an SMTP sender. Records the confirmed invocation input as output/receipt.
    """

    invocation = {}
    if isinstance(request.context, dict):
        invocation = request.context.get("invocation_input") or {}
    return CapabilityOutcome(
        "pass",
        output={"recorded": True, "invocation_input": invocation},
        receipt={"ok": True, "channel": "local_recorder"},
        claims=(
            {
                "kind": "executed",
                "subject": "communication.email.send",
                "value": True,
            },
        ),
    )


def build_default_work_runtime(
    *,
    db_path: str | Path,
    **work_kwargs: Any,
) -> WorkRuntime:
    """WorkRuntime with knowledge + local email recorder inventory."""

    verifiers = work_kwargs.pop("verifiers", None) or VerifierRegistry()
    inventory = work_kwargs.pop("profiles", None) or DeploymentInventory()
    runtime = build_work_runtime(
        db_path=db_path,
        profiles=inventory,
        verifiers=verifiers,
        **work_kwargs,
    )
    knowledge_store = KnowledgeStore(db_path)
    knowledge_store.initialize()
    register_knowledge_capabilities(
        inventory,
        verifiers,
        store=runtime.store,
        knowledge_store=knowledge_store,
    )
    if inventory.get("communication.email.send") is None:
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                implementation=CapabilityBinding(
                    "communication.email.send", "local", "record", "1"
                ),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
                side_effects=("external_email",),
            ),
            _local_email_recorder,
        )
    return runtime


def compose_services(
    *,
    work_db: str | Path,
    chat_db: str | Path,
    provider_config: str | Path | None = None,
    auth: AuthService | None = None,
    chat: ChatRuntime | None = None,
    advanced: AdvancedRuntime | None = None,
    work: WorkRuntime | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    **work_kwargs: Any,
) -> ApiServices:
    """Build the single Atlas API service graph.

    Route handlers must use these instances. They must not construct
    independent Chat/Advanced/Work topologies.
    """

    auth_service = auth if auth is not None else auth_from_env()
    chat_runtime = chat
    if chat_runtime is None:
        if provider_config is None:
            raise ValueError("provider_config or chat runtime is required")
        chat_runtime = build_chat_runtime(
            db_path=chat_db, provider_config=provider_config
        )
    advanced_runtime = advanced
    if advanced_runtime is None:
        if provider_config is None:
            raise ValueError("provider_config or advanced runtime is required")
        advanced_runtime = build_advanced_runtime(provider_config=provider_config)
    work_runtime = work
    if work_runtime is None:
        work_runtime = build_default_work_runtime(db_path=work_db, **work_kwargs)
    return ApiServices(
        chat=chat_runtime,
        advanced=advanced_runtime,
        work=work_runtime,
        auth=auth_service,
        host=host,
        port=port,
    )
