from __future__ import annotations

import re
from typing import Any


class SchemaValidationError(ValueError):
    pass


def validate_json(value: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    """Validate the small JSON-Schema subset Atlas runtime contracts need.

    Atlas deliberately avoids making a third-party validator a core runtime
    dependency. Unsupported schema keywords fail closed at registry validation
    time when introduced; this function covers the structural subset currently
    emitted by Atlas contracts and MCP tool schemas.
    """

    if not schema:
        return
    if "$ref" in schema:
        # External/meta-schema references describe schemas themselves and are not
        # runtime instance constraints at this boundary.
        return
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value is not in enum")

    expected = schema.get("type")
    if isinstance(expected, list):
        errors = []
        for candidate in expected:
            try:
                validate_json(value, {**schema, "type": candidate}, path=path)
                return
            except SchemaValidationError as exc:
                errors.append(str(exc))
        raise SchemaValidationError(f"{path}: value matches none of allowed types")
    if expected is not None:
        checks = {
            "object": lambda x: isinstance(x, dict),
            "array": lambda x: isinstance(x, list),
            "string": lambda x: isinstance(x, str),
            "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
            "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
            "boolean": lambda x: isinstance(x, bool),
            "null": lambda x: x is None,
        }
        if expected not in checks:
            raise SchemaValidationError(f"{path}: unsupported schema type {expected!r}")
        if not checks[expected](value):
            raise SchemaValidationError(f"{path}: expected {expected}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaValidationError(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    validate_json(value[key], child_schema, path=f"{path}.{key}")
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            extras = set(value) - set(properties)
            if extras:
                raise SchemaValidationError(
                    f"{path}: additional properties are not allowed: {sorted(extras)}"
                )

    if isinstance(value, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < int(min_items):
            raise SchemaValidationError(f"{path}: requires at least {min_items} items")
        max_items = schema.get("maxItems")
        if max_items is not None and len(value) > int(max_items):
            raise SchemaValidationError(f"{path}: allows at most {max_items} items")
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            raise SchemaValidationError(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_json(item, item_schema, path=f"{path}[{index}]")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < int(min_length):
            raise SchemaValidationError(f"{path}: string is shorter than {min_length}")
        max_length = schema.get("maxLength")
        if max_length is not None and len(value) > int(max_length):
            raise SchemaValidationError(f"{path}: string is longer than {max_length}")
        pattern = schema.get("pattern")
        if pattern and re.search(str(pattern), value) is None:
            raise SchemaValidationError(f"{path}: string does not match required pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            raise SchemaValidationError(f"{path}: value is below minimum {minimum}")
        maximum = schema.get("maximum")
        if maximum is not None and value > maximum:
            raise SchemaValidationError(f"{path}: value is above maximum {maximum}")
