from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_companion.cloud_providers import ProviderStateStore, build_registry
from atlas_companion.credentials import CredentialStore
from atlas_core.advanced import AdvancedRuntime, build_advanced_runtime
from atlas_core.capabilities import (
    CapabilityBinding,
    CapabilityOutcome,
    register_intelligence_capabilities,
)
from atlas_core.chat import ChatError, ChatRuntime, build_chat_runtime
from atlas_core.knowledge import KnowledgeStore, register_knowledge_capabilities
from atlas_core.providers import ModelProvider, ModelRouter, ProviderRegistry
from atlas_core.sources import LocalSourceDeployment, load_local_source_deployment
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
    local_sources: LocalSourceDeployment | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def close(self) -> None:
        if self.local_sources is not None:
            self.local_sources.close()


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
    """WorkRuntime with knowledge, local email recorder, and host intelligence."""

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
    _register_host_intelligence(inventory, work_kwargs.get("model_router"))
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
    registry = None
    if provider_config is not None and (
        chat is None or advanced is None or work is None
    ):
        registry = _host_provider_registry(
            provider_config,
            instance_root=Path(chat_db).expanduser().resolve().parent,
        )
    provider = None
    if chat is None or advanced is None:
        if registry is None:
            raise ValueError("provider_config or chat/advanced runtime is required")
        provider = _select_conversational_provider(registry)
    chat_runtime = chat
    if chat_runtime is None:
        chat_runtime = build_chat_runtime(db_path=chat_db, provider=provider)
    advanced_runtime = advanced
    if advanced_runtime is None:
        advanced_runtime = build_advanced_runtime(provider=provider)
    work_runtime = work
    local_sources = None
    if work_runtime is None:
        kwargs = dict(work_kwargs)
        if provider_config is not None and "local_source_registry" not in kwargs:
            local_sources = load_local_source_deployment(provider_config)
            kwargs["local_source_registry"] = local_sources.registry
            kwargs["local_source_kernel"] = local_sources.kernel
        if registry is not None:
            kwargs.setdefault("model_router", ModelRouter(registry))
        try:
            work_runtime = build_default_work_runtime(db_path=work_db, **kwargs)
        except BaseException:
            if local_sources is not None:
                local_sources.close()
            raise
    return ApiServices(
        chat=chat_runtime,
        advanced=advanced_runtime,
        work=work_runtime,
        auth=auth_service,
        local_sources=local_sources,
        host=host,
        port=port,
    )


def _host_provider_registry(
    provider_config: str | Path,
    *,
    instance_root: Path,
) -> ProviderRegistry:
    """Load overlay + ProviderStateStore + CredentialStore once.

    This is the host deployment provider truth. Chat/Advanced pick one
    enabled conversational provider from it. Work pins named keys from
    the same registry. API keys stay in CredentialStore, not JSON.
    """

    credentials = CredentialStore(instance_root)
    state = ProviderStateStore(instance_root)
    return build_registry(
        provider_config,
        credentials=credentials,
        state=state,
    )


def _select_conversational_provider(registry: ProviderRegistry) -> ModelProvider:
    enabled = [item for item in registry.providers() if item.spec.enabled]
    if not enabled:
        raise ChatError("ChatRuntime requires an enabled model provider.")
    return max(enabled, key=lambda item: (item.spec.priority, -item.spec.latency_rank))


def _host_eligible_provider_keys(registry: ProviderRegistry) -> tuple[str, ...]:
    """Provider identities this host may pin for generic text Work.

    Every key in the effective registry is an execution identity.
    Enablement is a live execute-time check. Overlay competence scores
    are ranking, not the allowlist. This list is not proof of
    multimodal document support.
    """

    return tuple(item.spec.key for item in registry.providers())


def _register_host_intelligence(inventory: DeploymentInventory, model_router) -> None:
    if model_router is None:
        return
    if inventory.get("reasoning.general") is not None:
        return
    keys = _host_eligible_provider_keys(model_router.registry)
    if not keys:
        return
    register_intelligence_capabilities(
        inventory,
        eligible_providers=keys,
        include_multimodal=False,
    )
