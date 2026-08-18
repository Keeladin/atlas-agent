from __future__ import annotations

from pathlib import Path

from atlas_core.capabilities import CapabilityRegistry, register_intelligence_capabilities
from atlas_core.integrations import register_morning_workflow
from atlas_core.knowledge import KnowledgeStore, register_knowledge_capabilities
from atlas_core.providers import ModelRouter, ProviderScoreStore, load_provider_registry
from atlas_core.runtime import RuntimeBudget, TaskRuntime
from atlas_core.tasks import TaskStore
from atlas_core.verification import VerifierRegistry


def build_runtime(
    *,
    db_path: str | Path,
    provider_config: str | Path | None = None,
    include_morning: bool = True,
    budget: RuntimeBudget | None = None,
) -> TaskRuntime:
    store = TaskStore(db_path)
    store.initialize()
    capabilities = CapabilityRegistry()
    verifiers = VerifierRegistry()
    register_intelligence_capabilities(capabilities)
    if include_morning:
        register_morning_workflow(capabilities, verifiers, task_store=store)
    knowledge_store = KnowledgeStore(db_path)
    knowledge_store.initialize()
    register_knowledge_capabilities(
        capabilities,
        verifiers,
        task_store=store,
        knowledge_store=knowledge_store,
    )
    model_router = None
    if provider_config is not None:
        score_store = ProviderScoreStore(db_path)
        score_store.initialize()
        model_router = ModelRouter(
            load_provider_registry(provider_config),
            score_store=score_store,
        )
    return TaskRuntime(store=store, capabilities=capabilities, verifiers=verifiers, model_router=model_router, budget=budget)
