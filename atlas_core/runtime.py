from __future__ import annotations

from atlas_core.capabilities import CapabilityRegistry
from atlas_core.events import EventBus
from atlas_core.providers import ModelRouter
from atlas_core.tasks import TaskStore
from atlas_core.verification import CompletionVerifier, VerifierRegistry
from atlas_core.context import ContextBuilder
from .runtime_types import RuntimeBudget, RuntimeResult, RecoveryResult
from .runtime_lifecycle import RuntimeLifecycleMixin
from .runtime_execution import RuntimeExecutionMixin
from .runtime_finish import RuntimeFinishMixin


class TaskRuntime(RuntimeLifecycleMixin, RuntimeExecutionMixin, RuntimeFinishMixin):
    """Durable Atlas task engine. Public facade over focused runtime mixins."""
    def __init__(
        self,
        *,
        store: TaskStore,
        capabilities: CapabilityRegistry,
        verifiers: VerifierRegistry | None = None,
        model_router: ModelRouter | None = None,
        event_bus: EventBus | None = None,
        budget: RuntimeBudget | None = None,
    ) -> None:
        self.store = store
        self.capabilities = capabilities
        self.verifiers = verifiers or VerifierRegistry()
        self.model_router = model_router
        self.event_bus = event_bus or EventBus()
        self.budget = budget or RuntimeBudget()
        self.context_builder = ContextBuilder(store)
        self.completion = CompletionVerifier(store)
