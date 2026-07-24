#!/usr/bin/env python3
"""Validate the JSON Schema subset used by Superflow without dependencies."""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any


class SchemaValidationError(ValueError):
    """The input does not conform to the schema."""


def _type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise SchemaValidationError(f"Unsupported schema reference: {reference}")
    current: Any = root
    for raw in reference[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise SchemaValidationError(f"Schema reference does not exist: {reference}")
        current = current[key]
    if not isinstance(current, dict):
        raise SchemaValidationError(f"Schema reference is not an object: {reference}")
    return current


def _valid(value: Any, schema: dict[str, Any], root: dict[str, Any]) -> bool:
    try:
        _validate(value, schema, root, "$")
        return True
    except SchemaValidationError:
        return False


def _validate(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
) -> None:
    if "$ref" in schema:
        _validate(value, _resolve_ref(root, schema["$ref"]), root, path)
        return

    if "allOf" in schema:
        for item in schema["allOf"]:
            _validate(value, item, root, path)
    if "oneOf" in schema:
        matches = sum(_valid(value, item, root) for item in schema["oneOf"])
        if matches != 1:
            raise SchemaValidationError(f"{path} must match exactly one oneOf branch")
    if "if" in schema and _valid(value, schema["if"], root) and "then" in schema:
        _validate(value, schema["then"], root, path)

    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{path} does not equal the required constant")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path} is not an allowed value")

    expected = schema.get("type")
    if isinstance(expected, str) and not _type_matches(value, expected):
        raise SchemaValidationError(f"{path} must have type {expected}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaValidationError(f"{path} is missing required field {key}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in properties:
                _validate(item, properties[key], root, child)
            elif schema.get("additionalProperties") is False:
                raise SchemaValidationError(f"{path} contains forbidden field {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate(item, schema["additionalProperties"], root, child)

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise SchemaValidationError(f"{path} has too few array items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaValidationError(f"{path} has too many array items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise SchemaValidationError(f"{path} array items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                _validate(item, schema["items"], root, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaValidationError(f"{path} is empty or too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaValidationError(f"{path} is too long")
        pattern = schema.get("pattern")
        if pattern and re.search(pattern, value) is None:
            raise SchemaValidationError(f"{path} does not match the required format")
        if schema.get("format") == "date-time":
            try:
                dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise SchemaValidationError(f"{path} is not a valid date-time") from exc

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaValidationError(f"{path} is below the minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaValidationError(f"{path} is above the maximum")


def validate(value: Any, schema: dict[str, Any]) -> None:
    """Validate a value against every schema keyword used by Superflow."""
    if not isinstance(schema, dict):
        raise SchemaValidationError("The schema must be an object")
    _validate(value, schema, schema, "$")
