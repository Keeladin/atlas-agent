from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Callable

from atlas_core.capabilities import (
    CapabilityArtifact,
    CapabilityBinding,
    CapabilityExecutionProfile,
    CapabilityOutcome,
    ExecutionBudget,
    RetryPolicy,
    require,
)
from atlas_core.tools import ToolConstraints, ToolDescriptor, ToolGateway, ToolOrigin, ToolResult
from .errors import LocalSourceError
from .local import LocalRootRegistry, LocalSourceKernel


_ERROR_STATUS = {
    "root_unknown": "blocked",
    "root_revision_unavailable": "blocked",
    "operation_not_allowed": "blocked",
    "invalid_path": "fail",
    "outside_root": "fail",
    "missing": "fail",
    "permission_denied": "blocked",
    "symlink_rejected": "fail",
    "wrong_type": "fail",
    "special_object_rejected": "fail",
    "too_large": "fail",
    "unsupported_encoding": "fail",
    "unsupported_platform": "blocked",
    "timeout": "abstain",
    "cancelled": "abstain",
    "drifted": "rework",
    "unreadable": "abstain",
    "internal_invariant": "fail",
}


def _input_schema(root_bindings: tuple[dict[str, Any], ...], operation: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "provider_namespace": {
            "type": "string",
            "enum": sorted({item["provider_namespace"] for item in root_bindings}),
        },
        "root_id": {
            "type": "string",
            "enum": sorted({item["root_id"] for item in root_bindings}),
        },
        "configuration_revision": {
            "type": "string",
            "enum": sorted({item["configuration_revision"] for item in root_bindings}),
        },
        "relative_path": {"type": "string", "minLength": 1, "maxLength": 4096},
    }
    if operation == "list":
        properties.update({
            "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
            "cursor": {"type": ["string", "null"], "minLength": 1},
        })
    return {
        "type": "object",
        "required": ["provider_namespace", "root_id", "configuration_revision", "relative_path"],
        "properties": properties,
        "additionalProperties": False,
        # Atlas extension: persisted and hashed with the WorkContract. The
        # adapter checks exact tuples; JSON-schema enums are only shape defense.
        "x-atlas-root-bindings": [
            {
                "provider_namespace": item["provider_namespace"],
                "root_id": item["root_id"],
                "configuration_revision": item["configuration_revision"],
            }
            for item in root_bindings
        ],
    }


def _data(request) -> dict[str, Any]:
    context = request.context if isinstance(request.context, dict) else {}
    value = context.get("invocation_input") or {}
    if not isinstance(value, dict):
        raise ValueError("Files invocation input must be an object.")
    return value


def _authorized(request, data: dict[str, Any]) -> None:
    asked = (
        str(data.get("provider_namespace") or ""),
        str(data.get("root_id") or ""),
        str(data.get("configuration_revision") or ""),
    )
    allowed = {
        (
            str(item.get("provider_namespace") or ""),
            str(item.get("root_id") or ""),
            str(item.get("configuration_revision") or ""),
        )
        for item in request.execution_policy.get("root_bindings", ())
        if isinstance(item, dict)
    }
    if asked not in allowed:
        raise LocalSourceError(
            "root_revision_unavailable",
            "Requested root identity was not frozen into this capability execution.",
            root_id=asked[1] or None,
            relative_path=str(data.get("relative_path") or "") or None,
        )


def _receipt(
    operation: str,
    *,
    observation: dict[str, Any] | None,
    status: str,
    error_code: str | None = None,
    entries_processed: int | None = None,
    bytes_processed: int | None = None,
    source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_ref = observation.get("source_ref") if observation else source_ref
    acquisition = observation.get("acquisition") if observation else {}
    return {
        "ok": status == "pass",
        "operation": operation,
        "provider": "local_sources",
        "kernel": "local-files-v1",
        "backend": acquisition.get("backend") if isinstance(acquisition, dict) else None,
        "source_ref": source_ref,
        "source_observation_id": observation.get("observation_id") if observation else None,
        "observation_payload_sha256": observation.get("observation_payload_sha256") if observation else None,
        "bytes_processed": bytes_processed,
        "entries_processed": entries_processed,
        "consistency": observation.get("consistency") if observation else None,
        "completeness": observation.get("completeness") if observation else None,
        "error_code": error_code,
    }


def _claim(request, operation: str, source_ref: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return ({
        "kind": "observed",
        "subject": f"files.{operation}:{source_ref['source_id']}",
        "value": {"operation": operation, "source_ref": source_ref},
        "criterion_ordinals": list(request.criterion_ordinals),
    },)


def _error_outcome(
    operation: str,
    error: LocalSourceError,
    *,
    source_ref: dict[str, Any] | None = None,
) -> CapabilityOutcome:
    status = _ERROR_STATUS[error.code]
    observation = None
    artifacts: tuple[CapabilityArtifact, ...] = ()
    if error.code == "drifted":
        raw = error.details.get("observation")
        artifact_payload = None
        if raw is None and isinstance(error.details.get("result"), dict):
            artifact_payload = error.details["result"]
            raw = artifact_payload.get("observation")
        if isinstance(raw, dict):
            observation = raw
            artifacts = (CapabilityArtifact(
                kind=f"files_{operation}_observation",
                payload=artifact_payload or {"observation": raw},
                provenance_category="acquired_observation",
                metadata={"source_consistency": "drifted"},
            ),)
    return CapabilityOutcome(
        status,
        receipt=_receipt(
            operation, observation=observation, status=status,
            error_code=error.code, source_ref=source_ref,
        ),
        error=f"{error.code}: {error.message}",
        artifacts=artifacts,
    )


def _handler(kernel: LocalSourceKernel, operation: str) -> Callable[[Any], CapabilityOutcome]:
    def execute(request) -> CapabilityOutcome:
        data = _data(request)
        source_ref = None
        try:
            _authorized(request, data)
            args = (
                str(data["provider_namespace"]),
                str(data["root_id"]),
                str(data["relative_path"]),
            )
            kwargs = {"configuration_revision": str(data["configuration_revision"])}
            source_ref = kernel.source_ref(*args).to_dict()
            if operation == "list":
                result = kernel.list(
                    *args,
                    page_size=int(data.get("page_size", 100)),
                    cursor=(str(data["cursor"]) if data.get("cursor") else None),
                    **kwargs,
                )
                observation = result.observation.to_dict()
                payload = {
                    "observation": observation,
                    "entries": [item.to_dict() for item in result.entries],
                    "next_cursor": result.next_cursor,
                    "entry_errors": list(result.entry_errors),
                }
                count = len(result.entries)
                return CapabilityOutcome(
                    "pass", output=payload, output_kind="files_list_observation",
                    output_provenance_category="acquired_observation",
                    receipt=_receipt(operation, observation=observation, status="pass", entries_processed=count),
                    metrics={"entries_processed": count},
                    claims=_claim(request, operation, observation["source_ref"]),
                )
            if operation == "stat":
                observed = kernel.stat(*args, **kwargs)
                observation = observed.to_dict()
                return CapabilityOutcome(
                    "pass", output={"observation": observation}, output_kind="files_stat_observation",
                    output_provenance_category="acquired_observation",
                    receipt=_receipt(operation, observation=observation, status="pass"),
                    claims=_claim(request, operation, observation["source_ref"]),
                )
            if operation == "hash":
                observed = kernel.hash(*args, **kwargs)
                observation = observed.to_dict()
                return CapabilityOutcome(
                    "pass", output={"observation": observation}, output_kind="files_hash_observation",
                    output_provenance_category="acquired_observation",
                    receipt=_receipt(operation, observation=observation, status="pass", bytes_processed=observed.byte_size),
                    metrics={"bytes_processed": observed.byte_size or 0},
                    claims=_claim(request, operation, observation["source_ref"]),
                )
            read = kernel.read(*args, **kwargs)
            observation = read.observation.to_dict()
            content = {
                "text": read.text,
                "encoding": read.encoding,
                "bom": read.bom,
                "media_type": read.observation.media_type,
                "source_observation_id": read.observation.observation_id,
                "source_observation_payload_sha256": read.observation.observation_payload_sha256,
                "source_byte_sha256": read.observation.byte_sha256,
                "source_ref": read.observation.source_ref.to_dict(),
            }
            return CapabilityOutcome(
                "pass", output={"observation": observation, "read": {"encoding": read.encoding, "bom": read.bom}},
                output_kind="files_read_observation",
                output_provenance_category="acquired_observation",
                artifacts=(CapabilityArtifact(
                    kind="files_acquired_content", payload=content,
                    provenance_category="acquired_content",
                    metadata={
                        "source_consistency": read.observation.consistency,
                        "source_observation_id": read.observation.observation_id,
                    },
                ),),
                receipt=_receipt(operation, observation=observation, status="pass", bytes_processed=read.observation.byte_size),
                metrics={"bytes_processed": read.observation.byte_size or 0},
                claims=_claim(request, operation, observation["source_ref"]),
            )
        except LocalSourceError as error:
            return _error_outcome(operation, error, source_ref=source_ref)

    return execute


def register_files_capabilities(
    inventory: Any,
    *,
    registry: LocalRootRegistry,
    kernel: LocalSourceKernel | None = None,
    gateway: ToolGateway,
) -> None:
    roots = registry.execution_policies()
    if not roots:
        return
    source_kernel = kernel or LocalSourceKernel(registry)
    retry = RetryPolicy(
        retry_on=("rework", "abstain"),
        stop_on=("pass", "fail", "blocked"),
    )
    for operation in ("list", "stat", "hash", "read"):
        capability_id = f"files.{operation}"
        if inventory.get(capability_id) is not None:
            continue
        require(capability_id)
        schema = _input_schema(roots, operation)
        tool_id = f"native.local_sources.{operation}"
        tool_version = "1.0.0"
        operation_executor = _handler(source_kernel, operation)

        def tool_handler(arguments, *, _execute=operation_executor, _roots=roots):
            synthetic = SimpleNamespace(
                context={"invocation_input": arguments},
                execution_policy={"root_bindings": list(_roots)},
                criterion_ordinals=(),
            )
            return ToolResult(
                True,
                output=_execute(synthetic),
                receipt={"ok": True, "provider": "local_sources"},
            )

        gateway.register(
            ToolDescriptor(
                id=tool_id,
                version=tool_version,
                description=f"Secure local-source {operation} provider operation.",
                required_authority="read",
                input_schema=schema,
                origin=ToolOrigin(
                    type="internal",
                    internal_handler=f"atlas_core.sources.{operation}",
                ),
                permissions=("read", "list") if operation == "list" else ("read",),
                constraints=ToolConstraints(
                    timeout_sec=10 if operation in {"list", "stat"} else 60,
                    read_only=True,
                ),
                privacy_level="internal",
                tags=("files", "local", "read_only"),
            ),
            tool_handler,
        )

        def capability_handler(request, *, _tool_id=tool_id, _version=tool_version):
            if request.surface is None:
                return CapabilityOutcome("fail", error="Files execution surface is unavailable.")
            result = request.surface.invoke(_tool_id, _data(request), version=_version)
            if not result.ok or not isinstance(result.output, CapabilityOutcome):
                return CapabilityOutcome(
                    "fail",
                    receipt={"ok": False, "provider": "local_sources"},
                    error=result.error or "Files provider returned an invalid outcome.",
                )
            outcome = result.output
            claims = tuple(
                {**claim, "criterion_ordinals": list(request.criterion_ordinals)}
                for claim in outcome.claims
            )
            return replace(outcome, claims=claims)

        output_kind = {
            "list": "files_list_observation",
            "stat": "files_stat_observation",
            "hash": "files_hash_observation",
            "read": "files_acquired_content",
        }[operation]
        inventory.register(
            CapabilityExecutionProfile(
                capability_id=capability_id,
                implementation=CapabilityBinding(
                    capability_id, "local_sources", operation, "1"
                ),
                version="1.0.0",
                executor_kind="tool",
                tools=(f"{tool_id}@{tool_version}",),
                input_schema=schema,
                output_schema={"type": "object"},
                output_kind=output_kind,
                verifier_id="core.receipt",
                idempotent=True,
                parallel_safe=False,
                privacy="local_only",
                budget=ExecutionBudget(
                    max_attempts=3,
                    timeout_seconds=10 if operation in {"list", "stat"} else 60,
                    max_context_chars=16_000,
                    max_output_chars=1_048_576,
                ),
                retry_policy=retry,
                tags=("files", "local", "read_only"),
            ),
            capability_handler,
        )
