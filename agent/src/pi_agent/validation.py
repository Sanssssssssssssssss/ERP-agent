"""Pi's JSON-schema tool argument normalization, translated to Python."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from pi_agent.messages import ToolCall
from pi_agent.tools import AgentTool


def validate_tool_arguments(tool: AgentTool, tool_call: ToolCall) -> dict[str, Any]:
    """Normalize, coerce, and validate exactly at Pi's tool-call boundary."""
    schema = dict(tool.parameters)
    args = deepcopy(tool_call.arguments)
    _normalize_optional_nulls(args, schema)
    coerced = _coerce(args, schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(coerced),
        key=lambda error: list(error.path),
    )
    if errors:
        details = "\n".join(f"  - {_error_path(error)}: {error.message}" for error in errors)
        raise ValueError(
            f'Validation failed for tool "{tool_call.name}":\n{details}\n\n'
            f"Received arguments:\n{tool_call.arguments}"
        )
    if not isinstance(coerced, dict):
        raise ValueError(f'Validation failed for tool "{tool_call.name}": root must be an object')
    return coerced


def _schema_types(schema: Mapping[str, Any]) -> list[str]:
    value = schema.get("type")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _matches_type(value: object, kind: str) -> bool:
    return {
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "string": isinstance(value, str),
        "null": value is None,
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(kind, False)


def _coerce_primitive(value: object, kind: str) -> object:
    if kind in {"number", "integer"}:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value)
            except ValueError:
                return value
            if isfinite(parsed) and kind == "number":
                return parsed
            if isfinite(parsed) and parsed.is_integer():
                return int(parsed)
    if kind == "boolean":
        if value is None:
            return False
        if value in ("true", 1):
            return True
        if value in ("false", 0):
            return False
    if kind == "string":
        if value is None:
            return ""
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (int, float)):
            return str(value)
    if kind == "null" and (value == "" or value == 0 or value is False):
        return None
    return value


def _coerce(value: Any, schema: Mapping[str, Any]) -> Any:
    for nested in schema.get("allOf", ()):
        value = _coerce(value, nested)
    for union in (schema.get("anyOf"), schema.get("oneOf")):
        if isinstance(union, list):
            value = _coerce_union(value, union)

    kinds = _schema_types(schema)
    if kinds and not (len(kinds) > 1 and any(_matches_type(value, kind) for kind in kinds)):
        for kind in kinds:
            candidate = _coerce_primitive(value, kind)
            if candidate is not value:
                value = candidate
                break

    if "object" in kinds and isinstance(value, dict):
        properties = schema.get("properties", {})
        for key, child in properties.items():
            if key in value:
                value[key] = _coerce(value[key], child)
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            for key in value.keys() - properties.keys():
                value[key] = _coerce(value[key], additional)
    if "array" in kinds and isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, list):
            for index, child in enumerate(items[: len(value)]):
                value[index] = _coerce(value[index], child)
        elif isinstance(items, dict):
            value[:] = [_coerce(item, items) for item in value]
    return value


def _coerce_union(value: Any, schemas: list[Mapping[str, Any]]) -> Any:
    if any(Draft202012Validator(schema).is_valid(value) for schema in schemas):
        return value
    for schema in schemas:
        candidate = _coerce(deepcopy(value), schema)
        if Draft202012Validator(schema).is_valid(candidate):
            return candidate
    return value


def _normalize_optional_nulls(value: Any, schema: Mapping[str, Any]) -> None:
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, list):
            for item, child in zip(value, items, strict=False):
                _normalize_optional_nulls(item, child)
        elif isinstance(items, dict):
            for item in value:
                _normalize_optional_nulls(item, items)
        return
    if not isinstance(value, dict):
        return
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    required = set(schema.get("required", ()))
    for key, child in properties.items():
        if key not in value:
            continue
        if (
            value[key] is None
            and key not in required
            and "$ref" not in child
            and not Draft202012Validator(child).is_valid(None)
        ):
            del value[key]
        else:
            _normalize_optional_nulls(value[key], child)


def _error_path(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if error.validator == "required":
        missing = next(iter(set(error.validator_value) - error.instance.keys()), None)
        return ".".join(part for part in (path, missing) if part)
    return path or "root"
