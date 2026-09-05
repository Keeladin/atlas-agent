from __future__ import annotations

import json
from typing import Any

from atlas_core.capabilities import CapabilityRegistry
from atlas_core.schema_validation import SchemaValidationError, validate_json

MAX_WORK_STEPS = 64
MAX_WORK_TEMPLATE_BYTES = 256 * 1024
_ALLOWED_STEP_KEYS = {"capability_id", "description", "input", "obligation_ids"}


class WorkflowValidationError(ValueError):
    pass


def validate_workflow_steps(registry: CapabilityRegistry, steps: list[dict[str, Any]]) -> None:
    """Preflight model-authored Work data against the live capability contracts.

    This validates structure only. It grants no authority and never executes a
    capability. Concrete inputs are validated again by CapabilityRuntime after
    $ref resolution when each step actually runs.
    """
    if not isinstance(steps, list) or not steps:
        raise WorkflowValidationError("work requires at least one step")
    if len(steps) > MAX_WORK_STEPS:
        raise WorkflowValidationError(f"work allows at most {MAX_WORK_STEPS} steps")
    encoded = json.dumps(steps, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_WORK_TEMPLATE_BYTES:
        raise WorkflowValidationError("work step template exceeds the maximum size")

    for ordinal, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise WorkflowValidationError(f"step {ordinal}: step must be an object")
        extras = set(step) - _ALLOWED_STEP_KEYS
        if extras:
            raise WorkflowValidationError(f"step {ordinal}: unsupported fields: {sorted(extras)}")
        capability_id = str(step.get("capability_id") or "").strip()
        if not capability_id:
            raise WorkflowValidationError(f"step {ordinal}: capability_id is required")
        description = str(step.get("description") or "").strip()
        if "description" in step and not description:
            raise WorkflowValidationError(f"step {ordinal}: description must not be empty")
        obligation_ids = step.get("obligation_ids", [])
        if not isinstance(obligation_ids, list) or not all(isinstance(item, str) and item.strip() for item in obligation_ids):
            raise WorkflowValidationError(f"step {ordinal}: obligation_ids must be an array of non-empty ids")
        if len(set(obligation_ids)) != len(obligation_ids):
            raise WorkflowValidationError(f"step {ordinal}: obligation_ids must be unique")
        payload = step.get("input")
        if not isinstance(payload, dict):
            raise WorkflowValidationError(f"step {ordinal}: input must be an object")
        try:
            registration = registry.get(capability_id)
        except KeyError as exc:
            raise WorkflowValidationError(f"step {ordinal}: unknown capability {capability_id}") from exc
        if registration.metadata.get("work_composable") is False:
            raise WorkflowValidationError(f"step {ordinal}: capability {capability_id} cannot be nested inside Work")
        try:
            _validate_template(payload, registration.definition.input_schema or {}, ordinal=ordinal, path=f"$.steps[{ordinal - 1}].input")
        except SchemaValidationError as exc:
            raise WorkflowValidationError(str(exc)) from exc


def _is_work_ref(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"$ref"}


def _validate_ref(value: dict[str, Any], *, ordinal: int, path: str) -> None:
    ref = value.get("$ref")
    if not isinstance(ref, dict) or set(ref) != {"step", "output"}:
        raise SchemaValidationError(f"{path}: work reference must contain exactly step and output")
    source = ref.get("step")
    if not isinstance(source, int) or isinstance(source, bool):
        raise SchemaValidationError(f"{path}: work reference step must be an integer")
    if source < 1 or source >= ordinal:
        raise SchemaValidationError(f"{path}: work reference must point to an earlier step")
    output = ref.get("output")
    if not isinstance(output, str) or (output and not output.startswith("/")):
        raise SchemaValidationError(f"{path}: work reference output must be a JSON pointer")


def _validate_template(value: Any, schema: dict[str, Any], *, ordinal: int, path: str) -> None:
    if _is_work_ref(value):
        _validate_ref(value, ordinal=ordinal, path=path)
        return
    if not schema or "$ref" in schema:
        _walk_refs(value, ordinal=ordinal, path=path)
        return

    expected = schema.get("type")
    if isinstance(expected, list):
        errors: list[str] = []
        for candidate in expected:
            try:
                _validate_template(value, {**schema, "type": candidate}, ordinal=ordinal, path=path)
                return
            except SchemaValidationError as exc:
                errors.append(str(exc))
        raise SchemaValidationError(f"{path}: value matches none of allowed types")

    if isinstance(value, dict) and expected in {None, "object"}:
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaValidationError(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            extras = set(value) - set(properties)
            if extras:
                raise SchemaValidationError(f"{path}: additional properties are not allowed: {sorted(extras)}")
        for key, child in value.items():
            child_schema = properties.get(key, {}) if isinstance(properties, dict) else {}
            _validate_template(child, child_schema if isinstance(child_schema, dict) else {}, ordinal=ordinal, path=f"{path}.{key}")
        return

    if isinstance(value, list) and expected in {None, "array"}:
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if min_items is not None and len(value) < int(min_items):
            raise SchemaValidationError(f"{path}: requires at least {min_items} items")
        if max_items is not None and len(value) > int(max_items):
            raise SchemaValidationError(f"{path}: allows at most {max_items} items")
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            raise SchemaValidationError(f"{path}: items must be unique")
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        for index, item in enumerate(value):
            _validate_template(item, item_schema, ordinal=ordinal, path=f"{path}[{index}]")
        return

    validate_json(value, schema, path=path)
    _walk_refs(value, ordinal=ordinal, path=path)


def _walk_refs(value: Any, *, ordinal: int, path: str) -> None:
    if _is_work_ref(value):
        _validate_ref(value, ordinal=ordinal, path=path)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _walk_refs(child, ordinal=ordinal, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_refs(child, ordinal=ordinal, path=f"{path}[{index}]")
