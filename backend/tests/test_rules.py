from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.rules import evaluate, validate_rules

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "score": {"type": "number"},
        "active": {"type": "boolean"},
        "dob": {"type": "string", "format": "date"},
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"doc_type": {"type": "string"}},
            },
        },
    },
}

EVALUATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _rule(condition: dict, rule_id: str = "r1") -> dict:
    return {"id": rule_id, "category": "completeness", "name": rule_id, "message": "m", "weight": 1, "condition": condition}


def _run(condition: dict, data: dict) -> tuple[bool, object]:
    result = evaluate([_rule(condition)], data, EVALUATED_AT)[0]
    return result.passed, result.actual


# ---- validation ----------------------------------------------------------


def test_validate_rejects_unknown_field() -> None:
    errors = validate_rules([_rule({"field": "nope", "op": "exists"})], SCHEMA)
    assert any("unknown field" in e for e in errors)


def test_validate_rejects_wrong_operator_for_type() -> None:
    errors = validate_rules([_rule({"field": "age", "op": "contains", "value": "x"})], SCHEMA)
    assert any("not valid for field" in e for e in errors)


def test_validate_rejects_duplicate_ids() -> None:
    rules = [_rule({"field": "name", "op": "exists"}, "r1"), _rule({"field": "age", "op": "exists"}, "r1")]
    errors = validate_rules(rules, SCHEMA)
    assert any("duplicate" in e for e in errors)


def test_validate_accepts_well_formed_rule() -> None:
    errors = validate_rules([_rule({"field": "name", "op": "exists"})], SCHEMA)
    assert errors == []


def test_validate_matches_rejects_bad_regex() -> None:
    errors = validate_rules([_rule({"field": "name", "op": "matches", "value": "("})], SCHEMA)
    assert any("regex" in e for e in errors)


def test_validate_any_item_checks_nested_field() -> None:
    good = _rule({"field": "documents", "op": "any_item", "item": {"field": "doc_type", "op": "exists"}})
    assert validate_rules([good], SCHEMA) == []

    bad = _rule({"field": "documents", "op": "any_item", "item": {"field": "nope", "op": "exists"}})
    assert any("unknown field" in e for e in validate_rules([bad], SCHEMA))


def test_validate_rejects_array_op_on_non_array_field() -> None:
    errors = validate_rules([_rule({"field": "name", "op": "count_gte", "value": 1})], SCHEMA)
    assert any("not an array field" in e for e in errors)


# ---- evaluation: table-driven over operator/type pairs -------------------


@pytest.mark.parametrize(
    "condition,data,expected",
    [
        ({"field": "name", "op": "exists"}, {"name": "a"}, True),
        ({"field": "name", "op": "exists"}, {}, False),
        ({"field": "name", "op": "not_exists"}, {}, True),
        ({"field": "name", "op": "is_empty"}, {"name": ""}, True),
        ({"field": "name", "op": "is_empty"}, {"name": "a"}, False),
        ({"field": "name", "op": "eq", "value": "a"}, {"name": "a"}, True),
        ({"field": "name", "op": "neq", "value": "a"}, {"name": "b"}, True),
        ({"field": "name", "op": "in", "value": ["a", "b"]}, {"name": "a"}, True),
        ({"field": "name", "op": "not_in", "value": ["a", "b"]}, {"name": "c"}, True),
        ({"field": "name", "op": "contains", "value": "bc"}, {"name": "abcd"}, True),
        ({"field": "name", "op": "matches", "value": "^a.*d$"}, {"name": "abcd"}, True),
        ({"field": "name", "op": "matches", "value": "^z"}, {"name": "abcd"}, False),
        ({"field": "age", "op": "gt", "value": 10}, {"age": 11}, True),
        ({"field": "age", "op": "gte", "value": 11}, {"age": 11}, True),
        ({"field": "age", "op": "lt", "value": 11}, {"age": 10}, True),
        ({"field": "age", "op": "lte", "value": 10}, {"age": 10}, True),
        ({"field": "score", "op": "between", "value": [1, 5]}, {"score": 3.2}, True),
        ({"field": "score", "op": "between", "value": [1, 5]}, {"score": 9}, False),
        ({"field": "active", "op": "is_true"}, {"active": True}, True),
        ({"field": "active", "op": "is_false"}, {"active": False}, True),
        ({"field": "dob", "op": "before", "value": "2000-01-01"}, {"dob": "1990-01-01"}, True),
        ({"field": "dob", "op": "after", "value": "2000-01-01"}, {"dob": "2010-01-01"}, True),
        ({"field": "dob", "op": "within_days", "value": 400}, {"dob": "2025-01-01"}, True),
        ({"field": "dob", "op": "older_than_days", "value": 100}, {"dob": "2000-01-01"}, True),
        ({"field": "documents", "op": "count_gte", "value": 1}, {"documents": [{"doc_type": "pan"}]}, True),
        ({"field": "documents", "op": "count_eq", "value": 0}, {"documents": []}, True),
        (
            {"field": "documents", "op": "any_item", "item": {"field": "doc_type", "op": "eq", "value": "pan"}},
            {"documents": [{"doc_type": "aadhaar"}, {"doc_type": "pan"}]},
            True,
        ),
        (
            {"field": "documents", "op": "all_item", "item": {"field": "doc_type", "op": "exists"}},
            {"documents": [{"doc_type": "pan"}, {}]},
            False,
        ),
    ],
)
def test_operator_table(condition: dict, data: dict, expected: bool) -> None:
    passed, _ = _run(condition, data)
    assert passed is expected


def test_missing_field_fails_cleanly_not_crash() -> None:
    passed, actual = _run({"field": "age", "op": "gt", "value": 5}, {})
    assert passed is False
    assert actual is None


def test_all_and_any_and_not_combinators() -> None:
    cond = {"all": [{"field": "name", "op": "exists"}, {"field": "age", "op": "gt", "value": 0}]}
    assert _run(cond, {"name": "a", "age": 1})[0] is True
    assert _run(cond, {"name": "a"})[0] is False

    cond_any = {"any": [{"field": "name", "op": "exists"}, {"field": "age", "op": "exists"}]}
    assert _run(cond_any, {"age": 1})[0] is True

    cond_not = {"not": {"field": "name", "op": "exists"}}
    assert _run(cond_not, {})[0] is True


def test_within_days_uses_evaluated_at_not_wall_clock() -> None:
    # Fixed evaluated_at means a replay years later gives the same result.
    result = evaluate(
        [_rule({"field": "dob", "op": "within_days", "value": 10})],
        {"dob": "2025-12-28"},
        EVALUATED_AT,
    )[0]
    assert result.passed is True
