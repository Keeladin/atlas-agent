from __future__ import annotations

from datetime import date
from pathlib import Path

from atlas_core.capabilities import CapabilityOutcome, CapabilityRegistry, CapabilitySpec, ExecutionBudget
from atlas_core.verification import VerificationResult, VerifierRegistry


def _morning_handler(request, *, task_store=None):
    from atlas_morning.config import load_aliases, load_config
    from atlas_morning.load import load_messages
    from atlas_morning.pack import build_pack, infer_operational_day, render_pack
    from atlas_morning.reconcile import load_corrections

    payload = {}
    direct_ids = request.direct_input_artifact_ids or request.input_artifact_ids
    if task_store is not None and direct_ids:
        payload = task_store.get_artifact(direct_ids[-1]).payload
    else:
        artifacts = request.context.get("artifacts", [])
        if artifacts:
            payload = artifacts[-1].get("payload") or {}
    if not isinstance(payload, dict):
        return CapabilityOutcome("fail", error="morning input artifact must be an object")
    input_path = payload.get("input")
    config_path = payload.get("config")
    if not input_path or not config_path:
        return CapabilityOutcome("fail", error="morning capability requires input and config paths")
    config = load_config(str(config_path))
    aliases = load_aliases(str(payload["aliases"])) if payload.get("aliases") else {}
    corrections = load_corrections(str(payload["corrections"])) if payload.get("corrections") else {}
    messages = load_messages(str(input_path))
    op_day = date.fromisoformat(str(payload["day"])) if payload.get("day") else infer_operational_day()
    pack = build_pack(messages, config, op_day, aliases=aliases, corrections=corrections)
    markdown = render_pack(pack)
    return CapabilityOutcome(
        "pass",
        output={"operational_day": op_day.isoformat(), "markdown": markdown},
        output_kind="morning_pack",
        receipt={"ok": True, "source": str(Path(input_path)), "operational_day": op_day.isoformat()},
        claims=(
            {"kind": "calculated", "subject": "morning_pack.operational_day", "value": op_day.isoformat()},
        ),
    )


def _verify_morning(spec, output, context):
    if not isinstance(output, dict):
        return VerificationResult("fail", "morning output is not structured")
    markdown = output.get("markdown")
    day = output.get("operational_day")
    if not isinstance(markdown, str) or not markdown.strip():
        return VerificationResult("rework", "morning pack rendered empty")
    required_columns = (
        "Machine / Item",
        "Reported period",
        "What happened",
        "Work / finding",
        "Last reported state",
        "Follow-up / unresolved",
    )
    missing = [column for column in required_columns if column not in markdown]
    if missing:
        return VerificationResult("rework", "morning pack is missing required columns", {"missing": missing})
    if not isinstance(day, str) or len(day) != 10:
        return VerificationResult("rework", "morning pack lacks operational-day identity")
    return VerificationResult("pass", "morning pack output contract present")


def register_morning_workflow(
    capabilities: CapabilityRegistry,
    verifiers: VerifierRegistry,
    *,
    task_store=None,
) -> None:
    verifiers.register("morning.output_contract", _verify_morning, replace=True)
    capabilities.register(
        CapabilitySpec(
            id="operations.morning_pack.generate",
            description="Generate the frozen V1 TMM morning pack from a configured source.",
            executor_kind="deterministic",
            required_authority="read",
            input_schema={
                "type": "object",
                "required": ["input", "config"],
                "properties": {
                    "input": {"type": "string", "minLength": 1},
                    "config": {"type": "string", "minLength": 1},
                    "aliases": {"type": ["string", "null"]},
                    "corrections": {"type": ["string", "null"]},
                    "day": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "required": ["operational_day", "markdown"],
                "properties": {
                    "operational_day": {"type": "string", "minLength": 10, "maxLength": 10},
                    "markdown": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
            output_kind="morning_pack",
            context_profile="execute",
            verifier_id="morning.output_contract",
            verification_required=True,
            idempotent=True,
            parallel_safe=False,
            privacy="local_only",
            budget=ExecutionBudget(max_attempts=2, max_context_chars=32_000),
        ),
        lambda request: _morning_handler(request, task_store=task_store),
    )
