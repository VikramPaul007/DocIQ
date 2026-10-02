from __future__ import annotations

from app.domain.schema_subset import field_paths, validate

VALID_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "properties": {
        "name": {"type": "string", "description": "Full name"},
        "age": {"type": "integer"},
        "dob": {"type": "string", "format": "date"},
        "active": {"type": "boolean"},
        "address": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"city": {"type": "string"}},
        },
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"doc_type": {"type": "string", "enum": ["pan", "aadhaar"]}},
            },
        },
    },
}


def test_valid_schema_passes() -> None:
    assert validate(VALID_SCHEMA) == []


def test_root_must_be_object() -> None:
    errors = validate({"type": "string"})
    assert any("type: object" in e for e in errors)


def test_ref_rejected() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"$ref": "#/definitions/foo"}},
    }
    errors = validate(schema)
    assert any("$ref" in e for e in errors)


def test_missing_additional_properties_false_rejected() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    errors = validate(schema)
    assert any("additionalProperties" in e for e in errors)


def test_unsupported_type_rejected() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"type": "null"}},
    }
    errors = validate(schema)
    assert any("unsupported type" in e for e in errors)


def test_unsupported_string_format_rejected() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"type": "string", "format": "email"}},
    }
    errors = validate(schema)
    assert any("format" in e for e in errors)


def test_required_field_must_exist_in_properties() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["missing"],
        "properties": {"x": {"type": "string"}},
    }
    errors = validate(schema)
    assert any("missing" in e for e in errors)


def test_depth_limit_enforced() -> None:
    schema: dict = {"type": "object", "additionalProperties": False, "properties": {}}
    node = schema
    for i in range(8):
        child = {"type": "object", "additionalProperties": False, "properties": {}}
        node["properties"][f"level{i}"] = child
        node = child
    node["properties"]["leaf"] = {"type": "string"}
    errors = validate(schema)
    assert any("depth" in e for e in errors)


def test_field_paths_extraction() -> None:
    paths = field_paths(VALID_SCHEMA)
    assert paths["name"] == "string"
    assert paths["dob"] == "string"
    assert paths["address.city"] == "string"
    assert paths["documents[].doc_type"] == "string"
    assert "address" not in paths  # only leaves, not container nodes
