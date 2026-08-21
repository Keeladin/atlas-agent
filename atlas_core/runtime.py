from __future__ import annotations

from atlas_core.capabilities import CapabilityRegistry
from atlas_core.events import EventBus
from atlas_core.providers import ModelRouter
from atlas_core.tasks import TaskStore
from atlas_core.verification import CompletionVerifier, OutcomeGate, SemanticOutcomeVerifier, VerifierRegistry
from atlas_core.context import ContextBuilder
from atlas_core.tools import ToolGateway
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
        tool_gateway: ToolGateway | None = None,
        budget: RuntimeBudget | None = None,
        outcome_gate: OutcomeGate | None = None,
    ) -> None:
        self.store = store
        self.capabilities = capabilities
        self.verifiers = verifiers or VerifierRegistry()
        self.model_router = model_router
        self.event_bus = event_bus or EventBus()
        self.tool_gateway = tool_gateway
        self.budget = budget or RuntimeBudget()
        self.context_builder = ContextBuilder(store)
        self.completion = CompletionVerifier(store)
        self.outcome_gate = outcome_gate or OutcomeGate(
            semantic=SemanticOutcomeVerifier(model_router) if model_router is not None else None
        )
        # WorkRuntime run-local surfaces, keyed by work_id then capability id.
        # Not a CapabilityRegistry slot. Dies when WorkEngine invokes handlers
        # directly (PR 5) and no longer uses TaskRuntime._execute_step.
        self.work_surfaces: dict[str, dict[str, object]] = {}
