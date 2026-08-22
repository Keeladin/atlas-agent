from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from atlas_core.capabilities import CapabilityOutcome, ExecutionBudget
from atlas_core.context import ContextPack
from atlas_core.providers import (
    ModelRequest,
    ModelRouter,
    ProviderRegistryError,
)
from atlas_core.runtime_types import RuntimeBudget

from .contract import ContractCapability, WorkContract
from .records import CLAIM_KINDS, ArtifactRecord, ExecutionRecord
from .store_common import UnknownRecordError


class ArtifactLookup(Protocol):
    def get_artifact(self, artifact_id: str) -> ArtifactRecord: ...


class WorkModelConsumer:
    """Contract-constrained model execution for Work.

    Provider selection is an implementation decision inside the frozen pin.
    The live registry cannot add keys, loosen privacy, or raise budgets.
    """

    def __init__(self, router: ModelRouter, store: ArtifactLookup) -> None:
        self._router = router
        self._store = store

    def execute(
        self,
        *,
        contract: WorkContract,
        pin: ContractCapability,
        pack: ContextPack,
        execution_id: str,
        previous: Sequence[ExecutionRecord],
        work_executions: Sequence[ExecutionRecord],
        set_provider: Callable[[str], None],
    ) -> CapabilityOutcome:
        if pin.executor_kind != "model":
            return CapabilityOutcome("fail", error="not a model capability")
        budget = pin.budget or ExecutionBudget()
        if (
            budget.max_context_chars
            and pack.chars > budget.max_context_chars
        ):
            return CapabilityOutcome(
                "fail",
                error=(
                    "context exceeds explicit budget: "
                    f"{pack.chars}>{budget.max_context_chars}"
                ),
            )
        work_budget = contract.work_budget
        model_calls = sum(1 for item in work_executions if item.provider)
        if model_calls >= work_budget.max_model_calls:
            return CapabilityOutcome(
                "fail",
                error="work model-call budget exhausted",
            )

        exclude = _excluded_provider_keys(previous)
        try:
            provider, reason = self._select(pin, pack.chars, exclude)
        except WorkModelError as exc:
            return CapabilityOutcome("fail", error=str(exc))

        projected = provider.spec.estimate_cost_usd(
            input_tokens=pack.tokens,
            output_tokens=max(1, ((budget.max_output_chars or 8_000) + 3) // 4),
        )
        cost_error = _cost_blocked(budget, work_budget, work_executions, projected)
        if cost_error is not None:
            return CapabilityOutcome("fail", error=cost_error)

        set_provider(provider.spec.key)
        try:
            response = provider.generate(
                ModelRequest(
                    capability_id=pin.capability_id,
                    system=_system_instruction(pack, pin),
                    input=pack.as_text(),
                    max_output_chars=budget.max_output_chars,
                    metadata={
                        "work_id": contract.work_id,
                        "execution_id": execution_id,
                        "context_manifest_id": pack.manifest.manifest_id,
                        "capability_version": pin.profile_version,
                    },
                )
            )
        except Exception as exc:
            return CapabilityOutcome(
                "abstain",
                error=str(exc),
                receipt={"ok": False, "provider": provider.spec.key},
            )
        metrics = dict(response.metrics)
        actual = provider.spec.estimate_cost_usd(
            input_tokens=int(metrics.get("input_tokens") or 0),
            output_tokens=int(metrics.get("output_tokens") or 0),
        )
        if actual is not None:
            metrics["estimated_cost_usd"] = actual
        receipt = {
            "ok": True,
            "provider": response.provider_key,
            "model": response.model,
            "reason": reason,
        }
        if pin.model_outcome_policy == "claim_bearing":
            try:
                output, claims, limitations = _normalize_claim_bearing_output(
                    response.text,
                    work_id=contract.work_id,
                    pack=pack,
                    store=self._store,
                )
            except ModelOutputError as exc:
                return CapabilityOutcome(
                    "rework",
                    metrics=metrics,
                    receipt=receipt,
                    error=f"claim-bearing model output rejected: {exc}",
                )
            receipt["model_output_limitations"] = limitations
            return CapabilityOutcome(
                "pass",
                output=output,
                output_kind=pin.output_kind or "capability_result",
                metrics=metrics,
                receipt=receipt,
                claims=claims,
            )
        return CapabilityOutcome(
            "pass",
            output=response.text,
            output_kind=pin.output_kind or "capability_result",
            metrics=metrics,
            receipt=receipt,
        )

    def _select(
        self,
        pin: ContractCapability,
        context_chars: int,
        exclude: tuple[str, ...],
    ):
        snapshots = pin.provider_snapshots
        if not snapshots:
            raise WorkModelError("no eligible provider")
        privacy = _effective_privacy(pin)
        excluded = set(exclude)
        candidates: list[tuple[tuple[float, int, int, int, int], Any, str]] = []
        skipped: list[str] = []
        for snapshot in snapshots:
            key = snapshot.key
            if key in excluded:
                skipped.append(f"{key} excluded after a prior attempt")
                continue
            try:
                provider = self._router.registry.get(key)
            except ProviderRegistryError:
                skipped.append(f"{key} is not registered")
                continue
            if not snapshot.matches(provider):
                skipped.append(
                    f"{key} live specification does not match the frozen pin"
                )
                continue
            spec = provider.spec
            if not spec.enabled:
                skipped.append(f"{key} disabled")
                continue
            if context_chars > snapshot.max_context_chars:
                skipped.append(
                    f"{key} context {context_chars} > max_context_chars "
                    f"{snapshot.max_context_chars}"
                )
                continue
            if privacy == "local_only" and not snapshot.local:
                skipped.append(f"{key} is not local")
                continue
            competence = self._router.competence(provider, pin.capability_id)
            score_value = 0.0 if competence is None else competence
            locality = 1 if snapshot.local else 0
            cloud_preference = (
                1 if privacy == "cloud_preferred" and not snapshot.local else 0
            )
            rank = (
                score_value,
                cloud_preference,
                spec.priority,
                locality,
                -spec.latency_rank,
            )
            candidates.append((rank, provider, key))
        if not candidates:
            detail = "; ".join(skipped) if skipped else "pin named no providers"
            raise WorkModelError(f"no eligible provider: {detail}")
        rank, provider, key = max(candidates, key=lambda item: item[0])
        return provider, (
            f"capability={pin.capability_id}; provider={key}; "
            f"competence={rank[0]:.3f}; privacy={privacy}; "
            f"context_chars={context_chars}"
        )


class WorkModelError(RuntimeError):
    pass


class ModelOutputError(ValueError):
    pass


def _effective_privacy(pin: ContractCapability) -> str:
    privacy = pin.privacy or "cloud_allowed"
    if pin.data_classification == "sensitive":
        return "local_only"
    return privacy


def _system_instruction(pack: ContextPack, pin: ContractCapability) -> str:
    profile = pack.payload.get("context_profile") or {}
    instruction = str(profile.get("instruction") or "")
    if pin.model_outcome_policy != "claim_bearing":
        return instruction
    return "\n".join(
        (
            instruction,
            "Return only strict JSON with exactly these keys: deliverable, claims, limitations.",
            "deliverable must be the requested user-facing text.",
            "claims must be an array of objects with kind, subject, statement, and evidence_artifact_ids.",
            "Only cite full-content artifact IDs supplied in this execution context.",
            "Use inferred or suggested for reasoning that has no evidence artifact; do not label it retrieved, observed, calculated, or executed.",
            "limitations must be an array of concise strings.",
        )
    )


def _normalize_claim_bearing_output(
    text: str,
    *,
    work_id: str,
    pack: ContextPack,
    store: ArtifactLookup,
) -> tuple[str, tuple[dict[str, Any], ...], tuple[str, ...]]:
    if pack.manifest.work_id != work_id:
        raise ModelOutputError("context manifest does not belong to this work")
    try:
        envelope = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ModelOutputError("response is not valid JSON") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"deliverable", "claims", "limitations"}:
        raise ModelOutputError("response must contain exactly deliverable, claims, and limitations")
    deliverable = envelope["deliverable"]
    if not isinstance(deliverable, str) or not deliverable.strip():
        raise ModelOutputError("deliverable must be a non-empty string")
    raw_claims = envelope["claims"]
    if not isinstance(raw_claims, list):
        raise ModelOutputError("claims must be an array")
    raw_limitations = envelope["limitations"]
    if not isinstance(raw_limitations, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_limitations
    ):
        raise ModelOutputError("limitations must be an array of non-empty strings")

    eligible = {
        item.id
        for item in pack.manifest.included
        if item.type == "artifact" and item.representation == "full"
    }
    claims: list[dict[str, Any]] = []
    for raw in raw_claims:
        if not isinstance(raw, dict) or set(raw) != {
            "kind", "subject", "statement", "evidence_artifact_ids"
        }:
            raise ModelOutputError("each claim must contain exactly kind, subject, statement, and evidence_artifact_ids")
        kind = raw["kind"]
        subject = raw["subject"]
        statement = raw["statement"]
        evidence = raw["evidence_artifact_ids"]
        if not isinstance(kind, str) or kind not in CLAIM_KINDS:
            raise ModelOutputError("claim kind is invalid")
        if not isinstance(subject, str) or not subject.strip():
            raise ModelOutputError("claim subject must be non-empty")
        if not isinstance(statement, str) or not statement.strip():
            raise ModelOutputError("claim statement must be non-empty")
        if not isinstance(evidence, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence
        ):
            raise ModelOutputError("claim evidence_artifact_ids must be an array of non-empty strings")
        evidence_ids = tuple(evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ModelOutputError("claim evidence_artifact_ids must not contain duplicates")
        if kind in {"observed", "retrieved", "calculated", "executed"} and not evidence_ids:
            raise ModelOutputError(f"{kind} claims require evidence")
        for artifact_id in evidence_ids:
            if artifact_id not in eligible:
                raise ModelOutputError("claim references an artifact without full context exposure")
            try:
                artifact = store.get_artifact(artifact_id)
            except UnknownRecordError as exc:
                raise ModelOutputError("claim references an unavailable artifact") from exc
            if artifact.work_id != work_id:
                raise ModelOutputError("claim references an artifact from another work")
        claims.append(
            {
                "kind": kind,
                "subject": subject.strip(),
                "value": {"statement": statement.strip()},
                "evidence_artifact_ids": evidence_ids,
            }
        )
    return deliverable.strip(), tuple(claims), tuple(item.strip() for item in raw_limitations)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _excluded_provider_keys(previous: Sequence[ExecutionRecord]) -> tuple[str, ...]:
    """Drop dead providers. Rework is not a provider death."""

    permanent: list[str] = []
    transient: list[str] = []
    seen_permanent: set[str] = set()
    seen_transient: set[str] = set()
    for item in previous:
        provider = item.provider
        if not provider:
            continue
        if item.status == "fail":
            if provider not in seen_permanent:
                permanent.append(provider)
                seen_permanent.add(provider)
        elif item.status == "abstain":
            if provider not in seen_transient:
                transient.append(provider)
                seen_transient.add(provider)
    return tuple(permanent + transient)


def _cost_blocked(
    budget: ExecutionBudget,
    work_budget: RuntimeBudget,
    previous: Sequence[ExecutionRecord],
    projected: float | None,
) -> str | None:
    if projected is None:
        return None
    if budget.max_cost_usd is not None and projected > budget.max_cost_usd:
        return (
            "projected provider cost exceeds capability budget: "
            f"{projected:.6f}>{budget.max_cost_usd:.6f}"
        )
    if work_budget.max_cost_usd is not None:
        spent = _spent_cost_usd(previous)
        if spent + projected > work_budget.max_cost_usd:
            return "projected provider cost exceeds remaining work budget"
    return None


def _spent_cost_usd(previous: Sequence[ExecutionRecord]) -> float:
    total = 0.0
    for item in previous:
        value = (item.metrics or {}).get("estimated_cost_usd")
        if value is not None:
            total += float(value)
    return total
