from __future__ import annotations

from atlas_core.context import ContextBuilder
from atlas_core.events import EventBus
from atlas_core.runtime_types import RecoveryResult, RuntimeResult
from .store import WorkStore
from atlas_core.tools import ToolGateway
from atlas_core.verification import CompletionVerifier, OutcomeGate, VerifierRegistry

from .contract import WorkContract
from .execution import WorkExecutionMixin
from .finish import WorkFinishMixin
from .lifecycle import WorkLifecycleMixin
from .model import WorkModelConsumer
from .resolve import ResolveReport


class WorkEngine(WorkLifecycleMixin, WorkExecutionMixin, WorkFinishMixin):
    """Native Work step-execution engine.

    Inputs are a persisted ``WorkContract`` and a ``ResolveReport`` already
    projected onto that contract. This class does not consult process-global
    executable sets and does not re-resolve pins. ``WorkRuntime.run`` calls
    ``run`` with those two objects.
    """

    def __init__(
        self,
        *,
        store: WorkStore,
        tools: ToolGateway,
        verifiers: VerifierRegistry | None = None,
        event_bus: EventBus | None = None,
        outcome_gate: OutcomeGate | None = None,
        model_consumer: WorkModelConsumer | None = None,
    ) -> None:
        self.store = store
        self.tools = tools
        self.verifiers = verifiers or VerifierRegistry()
        self.event_bus = event_bus or EventBus()
        self.context_builder = ContextBuilder(store)
        self.completion = CompletionVerifier(store)
        self.outcome_gate = outcome_gate or OutcomeGate()
        self.model_consumer = model_consumer

    def run(self, contract: WorkContract, report: ResolveReport) -> RuntimeResult:
        return self.run_until_blocked(contract, report)

    def recover(
        self, contract: WorkContract, report: ResolveReport
    ) -> RecoveryResult:
        return self.recover_interrupted(contract, report)

    def resume(self, contract: WorkContract, report: ResolveReport) -> int:
        return self.resume_blocked(contract, report)
