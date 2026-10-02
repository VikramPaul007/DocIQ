"""Validates templates' JSON Schema against our supported subset, and extracts
the flat field-path -> type map the rule engine and UI field picker use.

Supported: object/array/string/number/integer/boolean, string format=date,
required, description, enum. Objects must set additionalProperties: false.
$ref (local or remote) is rejected outright — supporting it would mean the
rule validator and field-path extractor would need to resolve and recurse
through references too, for a feature the Template Agent has no reason to
generate in the first place.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

MAX_DEPTH = 5
MAX_FIELDS = 200
SUPPORTED_TYPES = {"string", "number", "integer", "boolean", "object", "array"}
SUPPORTED_STRING_FORMATS = {None, "date"}


def validate(schema: dict[str, Any]) -> list[str]:
    """Returns a list of human-readable errors; an empty list means valid."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return [f"Not a valid JSON Schema: {exc.message}"]

    errors: list[str] = []
    if schema.get("type") != "object":
        errors.append("Root schema must have type: object")

    field_count = 0

    def walk(node: Any, path: str, depth: int) -> None:
        nonlocal field_count
        if not isinstance(node, dict):
            errors.append(f"{path or '<root>'}: must be an object")
            return
        if "$ref" in node:
            errors.append(f"{path or '<root>'}: $ref is not supported")
            return
        if depth > MAX_DEPTH:
            errors.append(f"{path or '<root>'}: exceeds max nesting depth of {MAX_DEPTH}")
            return

        node_type = node.get("type")
        if node_type is None:
            errors.append(f"{path or '<root>'}: missing 'type'")
            return
        if node_type not in SUPPORTED_TYPES:
            errors.append(f"{path or '<root>'}: unsupported type '{node_type}'")
            return

        if node_type == "string" and node.get("format") not in SUPPORTED_STRING_FORMATS:
            errors.append(f"{path or '<root>'}: unsupported string format '{node.get('format')}'")

        if node_type == "object":
            if node.get("additionalProperties") is not False:
                errors.append(f"{path or '<root>'}: object must set additionalProperties: false")
            properties = node.get("properties")
            if not isinstance(properties, dict) or not properties:
                errors.append(f"{path or '<root>'}: object must define at least one property")
                return
            required = node.get("required", [])
            for name in required:
                if name not in properties:
                    errors.append(
                        f"{path or '<root>'}: required field '{name}' is not in properties"
                    )
            for name, child in properties.items():
                field_count += 1
                if field_count > MAX_FIELDS:
                    errors.append(f"schema exceeds max field count of {MAX_FIELDS}")
                    return
                walk(child, f"{path}.{name}" if path else name, depth + 1)

        elif node_type == "array":
            items = node.get("items")
            if not isinstance(items, dict):
                errors.append(f"{path}: array must define 'items'")
                return
            walk(items, f"{path}[]", depth + 1)

    walk(schema, "", 0)
    return errors


def field_paths(schema: dict[str, Any]) -> dict[str, str]:
    """Flat {path: json type} map, e.g. {"name": "string", "docs[].type":
    "string"}. Assumes ``validate()`` already passed on this schema."""
    paths: dict[str, str] = {}

    def walk(node: dict[str, Any], path: str) -> None:
        node_type: str = node["type"]
        if node_type == "object":
            for name, child in node.get("properties", {}).items():
                walk(child, f"{path}.{name}" if path else name)
        elif node_type == "array":
            walk(node.get("items", {}), f"{path}[]")
        else:
            paths[path] = node_type

    walk(schema, "")
    return paths


def field_formats(schema: dict[str, Any]) -> dict[str, str | None]:
    """Parallel to ``field_paths``, but the string ``format`` (e.g. "date")
    instead of the type — the rule engine uses this to tell a plain string
    field from a date field, since both report type "string"."""
    formats: dict[str, str | None] = {}

    def walk(node: dict[str, Any], path: str) -> None:
        node_type: str = node["type"]
        if node_type == "object":
            for name, child in node.get("properties", {}).items():
                walk(child, f"{path}.{name}" if path else name)
        elif node_type == "array":
            walk(node.get("items", {}), f"{path}[]")
        else:
            formats[path] = node.get("format")

    walk(schema, "")
    return formats


def array_paths(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Maps each array field's dotted path (no trailing ``[]``) to its item
    subschema, e.g. {"documents": {...}} — used by the rule engine's array
    operators (count_*, any_item, all_item). Doesn't recurse into an array's
    own items looking for further nested arrays: any_item/all_item wrap one
    level of nested condition, which covers the documents-list-of-objects
    shape this platform's templates actually use."""
    result: dict[str, dict[str, Any]] = {}

    def walk(node: dict[str, Any], path: str) -> None:
        node_type = node.get("type")
        if node_type == "object":
            for name, child in node.get("properties", {}).items():
                walk(child, f"{path}.{name}" if path else name)
        elif node_type == "array":
            result[path] = node.get("items", {})

    walk(schema, "")
    return result
