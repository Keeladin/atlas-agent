from __future__ import annotations

from typing import Any

from atlas_core.schema_validation import project_object_to_schema
from .records import StepRecord
from .store_common import _payload_hash


def direct_request_artifact_ids(store, step: StepRecord) -> tuple[str, ...]:
    request_ids: list[str] = []
    for artifact_id in step.input_artifact_ids:
        artifact = store.get_artifact(artifact_id)
        if artifact.kind != "task_brief":
            request_ids.append(artifact_id)
    if not request_ids:
        return ()
    return (request_ids[-1],)


def project_invocation_input(store, step: StepRecord, pin) -> Any:
    payloads = [
        store.get_artifact(artifact_id).payload
        for artifact_id in direct_request_artifact_ids(store, step)
    ]
    schema = pin.input_schema
    if len(payloads) == 1:
        return project_object_to_schema(payloads[0], schema)
    if payloads:
        return {"artifacts": payloads}
    return {}


def confirmation_document(store, step: StepRecord, pin) -> dict[str, Any]:
    """Execution-semantic payload that a confirmation is bound to.

    Ambient runtime state (work status, attempt counts, context packs,
    live ranking, enabled flags) is excluded. Changing any included field
    invalidates a prior confirmation.
    """

    invocation = project_invocation_input(store, step, pin)
    binding = None if pin.binding is None else pin.binding.as_dict()
    return {
        "binding": binding,
        "capability_id": pin.capability_id,
        "executor_kind": pin.executor_kind,
        "invocation_input": invocation,
        "profile_version": pin.profile_version or "0.0.0",
        "provider_snapshots": [item.as_dict() for item in pin.provider_snapshots],
        "step_id": step.id,
        "tools": list(pin.tools),
        "work_id": step.work_id,
    }


def confirmation_digest(document: dict[str, Any]) -> tuple[str, str]:
    return _payload_hash(document)


def confirmation_summary(capability_id: str, invocation_input: Any) -> str:
    payload = invocation_input if isinstance(invocation_input, dict) else {}
    if capability_id == "communication.email.send":
        destination = (
            payload.get("to")
            or payload.get("recipient")
            or payload.get("destination")
        )
        subject = payload.get("subject")
        if destination and subject:
            return (
                f"Atlas wants to send this email to {destination} "
                f"with subject {subject}"
            )
        if destination:
            return f"Atlas wants to send this email to {destination}"
        return "Atlas wants to send an email"
    if capability_id == "automation.workflow.execute":
        workflow = (
            payload.get("workflow")
            or payload.get("workflow_id")
            or payload.get("id")
            or "the requested workflow"
        )
        return (
            f"Atlas wants to execute workflow {workflow} with these parameters"
        )
    if capability_id.startswith("automation.workflow"):
        name = (
            payload.get("name")
            or payload.get("workflow")
            or "the requested workflow"
        )
        return f"Atlas wants to create workflow {name} with these parameters"
    return f"Atlas wants to execute {capability_id} with these parameters"
