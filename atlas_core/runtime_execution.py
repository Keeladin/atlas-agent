from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from atlas_core.authority import authority_allows
from atlas_core.capabilities import CapabilityOutcome, CapabilityRegistryError, CapabilityRequest
from atlas_core.capabilities.execution import CapabilityExecutionProfile
from atlas_core.context import ContextBuilder
from atlas_core.events import RuntimeEvent
from atlas_core.knowledge import ingest_request_from_task, search_query_from_task
from atlas_core.providers import ModelRequest, ModelRoutingError
from atlas_core.tasks import InvalidTransitionError, StepRecord
from atlas_core.schema_validation import SchemaValidationError, validate_json
from .runtime_types import RuntimeResult, RecoveryResult

_REQUEST_KINDS = {
    "knowledge.search": "knowledge_search_request",
    "knowledge.ingest_text": "knowledge_ingest_request",
}
_AUTH_HTTP_CODES = {401, 403}
_HTTP_STATUS_RE = re.compile(r"\bHTTP (\d{3})\b")


def _http_status_from_provider_error(error: str | None) -> int | None:
    if not error:
        return None
    match = _HTTP_STATUS_RE.search(error)
    return int(match.group(1)) if match else None


def _provider_failure_is_permanent(status: str, error: str | None) -> bool:
    if status == "fail":
        return True
    if status != "abstain":
        return False
    if "credential is not configured" in (error or "").lower():
        return True
    return _http_status_from_provider_error(error) in _AUTH_HTTP_CODES


def _exclude_provider_keys(
    previous: tuple[Any, ...],
    *,
    remaining_route_exists: Callable[[tuple[str, ...]], bool],
) -> tuple[str, ...]:
    """Exclude dead providers without exiling a sole transient failure.

    Fail and auth-like abstain are always excluded. Other abstain (timeout,
    connection error) is excluded only when another candidate can still be
    selected. Rework is never treated as a provider death.
    """
    permanent: list[str] = []
    transient: list[str] = []
    seen_permanent: set[str] = set()
    seen_transient: set[str] = set()
    for item in previous:
        provider = item.provider
        if not provider:
            continue
        if item.status == "fail" or _provider_failure_is_permanent(item.status, item.error):
            if provider not in seen_permanent:
                permanent.append(provider)
                seen_permanent.add(provider)
        elif item.status == "abstain" and provider not in seen_transient:
            transient.append(provider)
            seen_transient.add(provider)
    proposed = tuple(dict.fromkeys([*permanent, *transient]))
    if not proposed:
        return ()
    if remaining_route_exists(proposed):
        return proposed
    return tuple(permanent)

class RuntimeExecutionMixin:
    def _execute_step(self, step: StepRecord) -> bool:
        if not step.capability:
            self.store.set_step_status(step.id, "failed")
            self._emit(
                step.task_id,
                "step.failed",
                step_id=step.id,
                payload={"reason": "step has no capability"},
            )
            return True

        try:
            binding = self.capabilities.get(step.capability, step.capability_version)
        except CapabilityRegistryError as exc:
            execution = self.store.begin_execution(
                step.task_id,
                step_id=step.id,
                capability=step.capability,
                capability_version=step.capability_version or "1.0.0",
            )
            self._emit(
                step.task_id,
                "capability.started",
                step_id=step.id,
                execution_id=execution.id,
                payload={"capability": step.capability, "attempt": execution.attempt},
            )
            self.store.finish_execution(
                execution.id,
                status="fail",
                error=str(exc),
            )
            self._emit(
                step.task_id,
                "capability.completed",
                step_id=step.id,
                execution_id=execution.id,
                payload={"capability": step.capability, "status": "fail"},
            )
            return True

        definition = binding.definition
        profile = binding.profile
        task = self.store.get_task(step.task_id)
        try:
            step = self._ensure_invocation_artifacts(step, profile, task)
        except ValueError as exc:
            execution = self.store.begin_execution(
                step.task_id,
                step_id=step.id,
                capability=profile.capability_id,
                capability_version=profile.version,
            )
            self.store.finish_execution(execution.id, status="fail", error=str(exc))
            self._emit(
                step.task_id,
                "capability.completed",
                step_id=step.id,
                execution_id=execution.id,
                payload={"capability": profile.capability_id, "status": "fail"},
            )
            return True

        approved_override = self._approved_for_step(
            step,
            definition.required_authority,
        )

        if profile.executor_kind == "human":
            if approved_override is None:
                self._request_authority_approval(
                    step,
                    definition.required_authority,
                    requested_action=step.description,
                )
                return True
            return self._complete_human_step(step, profile, approved_override)

        if (
            not authority_allows(task.authority_scope, definition.required_authority)
            and approved_override is None
        ):
            self._request_authority_approval(step, definition.required_authority)
            return True

        previous = self.store.list_executions(step.task_id, step_id=step.id)
        if len(previous) >= profile.budget.max_attempts:
            self.store.set_step_status(step.id, "failed")
            self._emit(
                step.task_id,
                "step.failed",
                step_id=step.id,
                payload={
                    "reason": "capability attempt limit reached",
                    "max_attempts": profile.budget.max_attempts,
                },
            )
            return True

        input_ids = self.store.dependency_output_artifact_ids(step.id)
        execution = self.store.begin_execution(
            step.task_id,
            step_id=step.id,
            capability=profile.capability_id,
            capability_version=profile.version,
            input_artifact_ids=input_ids,
        )
        self._emit(
            step.task_id,
            "capability.started",
            step_id=step.id,
            execution_id=execution.id,
            payload={
                "capability": profile.capability_id,
                "capability_version": profile.version,
                "provider": None,
                "attempt": execution.attempt,
            },
        )

        try:
            tool_descriptors = ()
            pinned_tools = tuple(step.metadata.get("allowed_tools") or ())
            tool_refs = pinned_tools or profile.tools
            if tool_refs:
                if self.tool_gateway is None:
                    raise ValueError(
                        "capability contract declares allowed_tools but no ToolGateway is configured"
                    )
                tool_descriptors = tuple(
                    descriptor.as_context_dict()
                    for descriptor in self.tool_gateway.descriptors(tool_refs)
                )
            previous_manifest_id = None
            failure_reason = None
            if previous:
                prior_manifest = self.store.context_manifest_for_execution(previous[-1].id)
                previous_manifest_id = prior_manifest.id if prior_manifest else None
                if previous[-1].status in {"rework", "abstain", "fail", "blocked"}:
                    failure_reason = previous[-1].error or previous[-1].status
            pack = self.context_builder.build(
                step.task_id,
                step.id,
                artifact_ids=input_ids,
                execution_id=execution.id,
                registration=binding,
                max_chars=profile.budget.max_context_chars,
                tool_descriptors=tool_descriptors,
                required_artifact_ids=tuple(step.input_artifact_ids),
                previous_manifest_id=previous_manifest_id,
                failure_reason=failure_reason,
            )
            try:
                validate_json(
                    pack.payload.get("invocation_input"),
                    profile.input_schema,
                    path="$.invocation_input",
                )
            except SchemaValidationError as exc:
                raise ValueError(f"input schema validation failed: {exc}") from exc
            manifest_record = self.store.write_context_manifest(
                step.task_id,
                step_id=step.id,
                execution_id=execution.id,
                capability=profile.capability_id,
                capability_version=profile.version,
                assembler_version=pack.manifest.assembler_version,
                budget_tokens=pack.manifest.budget_tokens,
                total_tokens=pack.manifest.total_tokens,
                manifest=pack.manifest.as_dict(),
                manifest_id=pack.manifest.manifest_id,
            )
            self._emit(
                step.task_id,
                "context.manifest.written",
                step_id=step.id,
                execution_id=execution.id,
                payload={
                    "manifest_id": manifest_record.id,
                    "sha256": manifest_record.sha256,
                    "capability": profile.capability_id,
                    "capability_version": profile.version,
                    "tokens": pack.manifest.total_tokens,
                    "budget": pack.manifest.budget_tokens,
                },
            )
        except Exception as exc:
            self.store.finish_execution(
                execution.id,
                status="fail",
                error=f"context assembly failed: {exc}",
            )
            self._emit(
                step.task_id,
                "capability.completed",
                step_id=step.id,
                execution_id=execution.id,
                payload={"capability": profile.capability_id, "status": "fail"},
            )
            self.store.create_checkpoint(
                step.task_id,
                reason=f"step {step.id} context assembly failed",
            )
            return True

        if profile.executor_kind == "model":
            outcome = self._execute_model_frame(
                step,
                profile,
                execution.id,
                pack,
                previous,
            )
        else:
            direct_ids = tuple(step.input_artifact_ids)
            direct_set = set(direct_ids)
            dependency_ids = tuple(
                artifact_id
                for artifact_id in input_ids
                if artifact_id not in direct_set
            )
            request = CapabilityRequest(
                step.task_id,
                step.id,
                profile.capability_id,
                pack.payload,
                input_ids,
                execution.attempt,
                capability_version=profile.version,
                direct_input_artifact_ids=direct_ids,
                dependency_artifact_ids=dependency_ids,
                idempotency_key=f"{step.task_id}:{step.id}:{profile.capability_id}",
                work_id=step.task_id,
                # Temporary: WorkRuntime.work_surfaces bridge. Remove with WorkEngine.
                surface=(
                    getattr(self, "work_surfaces", {}).get(step.task_id, {}).get(
                        step.capability
                    )
                ),
            )
            try:
                outcome = (
                    binding.handler(request)
                    if binding.handler is not None
                    else CapabilityOutcome(
                        "fail",
                        error="capability has no handler",
                    )
                )
            except Exception as exc:
                outcome = CapabilityOutcome("fail", error=str(exc))

        self._finish_frame(
            step,
            profile,
            execution.id,
            pack.payload,
            outcome,
        )
        return True

    def _execute_model_frame(
        self,
        step: StepRecord,
        profile: CapabilityExecutionProfile,
        execution_id: str,
        pack: Any,
        previous: tuple[Any, ...],
    ) -> CapabilityOutcome:
        if self.model_router is None:
            return CapabilityOutcome(
                "blocked",
                error="model capability has no configured model router",
            )
        model_calls = sum(
            1
            for item in self.store.list_executions(step.task_id)
            if item.provider
        )
        if model_calls >= self.budget.max_model_calls:
            return CapabilityOutcome(
                "blocked",
                error="task model-call budget exhausted",
            )
        def remaining_route_exists(exclude: tuple[str, ...]) -> bool:
            try:
                self.model_router.select(
                    profile.capability_id,
                    context_chars=pack.chars,
                    privacy=profile.privacy,
                    eligible_providers=profile.eligible_providers,
                    exclude_provider_keys=exclude,
                )
            except ModelRoutingError:
                return False
            return True

        failed_providers = _exclude_provider_keys(
            previous,
            remaining_route_exists=remaining_route_exists,
        )
        try:
            route = self.model_router.select(
                profile.capability_id,
                context_chars=pack.chars,
                privacy=profile.privacy,
                eligible_providers=profile.eligible_providers,
                exclude_provider_keys=failed_providers,
            )
        except ModelRoutingError as exc:
            return CapabilityOutcome("blocked", error=str(exc))

        estimated_input_tokens = pack.tokens
        estimated_output_tokens = max(
            1,
            ((profile.budget.max_output_chars or 8_000) + 3) // 4,
        )
        projected_call_cost = route.provider.spec.estimate_cost_usd(
            input_tokens=estimated_input_tokens,
            output_tokens=estimated_output_tokens,
        )
        if (
            profile.budget.max_cost_usd is not None
            and projected_call_cost is not None
            and projected_call_cost > profile.budget.max_cost_usd
        ):
            return CapabilityOutcome(
                "blocked",
                error=(
                    "projected provider cost exceeds capability budget: "
                    f"{projected_call_cost:.6f}>{profile.budget.max_cost_usd:.6f}"
                ),
            )
        if (
            self.budget.max_cost_usd is not None
            and projected_call_cost is not None
            and self._spent_cost_usd(step.task_id) + projected_call_cost
            > self.budget.max_cost_usd
        ):
            return CapabilityOutcome(
                "blocked",
                error="projected provider cost exceeds remaining task budget",
            )

        self.store.set_execution_provider(execution_id, route.provider.spec.key)
        self._emit(
            step.task_id,
            "provider.selected",
            step_id=step.id,
            execution_id=execution_id,
            payload={
                "provider": route.provider.spec.key,
                "reason": route.reason,
            },
        )
        try:
            response = route.provider.generate(
                ModelRequest(
                    capability_id=profile.capability_id,
                    system=pack.payload["context_profile"]["instruction"],
                    input=pack.as_text(),
                    max_output_chars=profile.budget.max_output_chars,
                    metadata={
                        "task_id": step.task_id,
                        "step_id": step.id,
                        "context_manifest_id": pack.manifest.manifest_id,
                        "capability_version": profile.version,
                    },
                )
            )
            metrics = dict(response.metrics)
            actual_cost = route.provider.spec.estimate_cost_usd(
                input_tokens=int(metrics.get("input_tokens") or 0),
                output_tokens=int(metrics.get("output_tokens") or 0),
            )
            if actual_cost is not None:
                metrics["estimated_cost_usd"] = actual_cost
            return CapabilityOutcome(
                "pass",
                output=response.text,
                output_kind=profile.output_kind,
                metrics=metrics,
                receipt={
                    "ok": True,
                    "provider": response.provider_key,
                    "model": response.model,
                },
            )
        except Exception as exc:
            return CapabilityOutcome(
                "abstain",
                error=str(exc),
                receipt={
                    "ok": False,
                    "provider": route.provider.spec.key,
                },
            )

    def _ensure_invocation_artifacts(self, step: StepRecord, profile: CapabilityExecutionProfile, task) -> StepRecord:
        required = list((profile.input_schema or {}).get("required") or [])
        if not required or step.input_artifact_ids:
            return step
        payload = self._invocation_payload_from_task(profile, task, step)
        if payload is None:
            raise ValueError(
                f"{profile.capability_id} requires {required} in a direct input artifact; "
                "this step was planned without one."
            )
        artifact = self.store.put_artifact(
            task.id,
            step_id=step.id,
            kind=_REQUEST_KINDS.get(profile.capability_id, "capability_request"),
            payload=payload,
            metadata={"synthesized_invocation_input": True, "capability": profile.capability_id},
        )
        return self.store.set_step_input_artifact_ids(step.id, (artifact.id,))

    @staticmethod
    def _invocation_payload_from_task(profile: CapabilityExecutionProfile, task, step: StepRecord) -> dict[str, Any] | None:
        if profile.capability_id == "knowledge.search":
            return {
                "query": search_query_from_task(
                    objective=task.objective,
                    description=step.description,
                ),
                "limit": 8,
            }
        if profile.capability_id == "knowledge.ingest_text":
            return ingest_request_from_task(
                objective=task.objective,
                description=step.description,
            )
        return None

    def _complete_human_step(self, step: StepRecord, profile: CapabilityExecutionProfile, approval: Any) -> bool:
        previous = self.store.list_executions(step.task_id, step_id=step.id)
        if len(previous) >= profile.budget.max_attempts:
            self.store.set_step_status(step.id, "failed")
            return True
        input_ids = self.store.dependency_output_artifact_ids(step.id)
        execution = self.store.begin_execution(
            step.task_id,
            step_id=step.id,
            capability=profile.capability_id,
            capability_version=profile.version,
            provider="human",
            input_artifact_ids=input_ids,
        )
        self._emit(
            step.task_id,
            "capability.started",
            step_id=step.id,
            execution_id=execution.id,
            payload={"capability": profile.capability_id, "provider": "human"},
        )
        outcome = CapabilityOutcome(
            "pass",
            output={
                "approval_id": approval.id,
                "decision": approval.status,
                "note": approval.decision_note,
                "requested_action": approval.requested_action,
            },
            output_kind=profile.output_kind or "human_decision",
            receipt={"ok": True, "approval_id": approval.id},
            claims=(
                {
                    "kind": "executed",
                    "subject": f"human_gate.{profile.capability_id}",
                    "value": approval.status,
                },
            ),
        )
        self._finish_frame(step, profile, execution.id, {}, outcome)
        return True
