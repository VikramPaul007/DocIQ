"""Deterministic rule engine: pure functions, no I/O.

Rules are small JSON ASTs, validated against the template's own schema (a
rule can never reference a field that doesn't exist, or use an operator the
field's type doesn't support) and evaluated against extracted data to
produce stable, explainable results — see "Eliminating Hallucinations via
Typed Operands" in docs/technical-implementation-plan.md.

``evaluate()`` is total: a missing field or malformed condition produces a
defined (failing) result, never an exception — a bad rule must not crash the
pipeline, and the audit trail needs *something* to point to either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import re2

from app.domain.schema_subset import array_paths, field_formats, field_paths

CATEGORIES = {"completeness", "validation", "eligibility", "risk", "compliance", "business"}

ANY_TYPE_OPS = {"exists", "not_exists", "is_empty"}
STRING_OPS = {"eq", "neq", "in", "not_in", "contains", "matches"}
NUMERIC_OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "between"}
DATE_OPS = {"before", "after", "within_days", "older_than_days"}
BOOLEAN_OPS = {"is_true", "is_false"}
ARRAY_OPS = {"count_eq", "count_gte", "count_lte", "any_item", "all_item"}

MAX_PATTERN_LENGTH = 200
_MISSING = object()


@dataclass
class RuleResult:
    rule_id: str
    category: str
    passed: bool
    message: str
    actual: Any


def _ops_for_logical_type(logical_type: str) -> set[str]:
    if logical_type == "string":
        return ANY_TYPE_OPS | STRING_OPS
    if logical_type in ("number", "integer"):
        return ANY_TYPE_OPS | NUMERIC_OPS
    if logical_type == "date":
        return ANY_TYPE_OPS | DATE_OPS
    if logical_type == "boolean":
        return ANY_TYPE_OPS | BOOLEAN_OPS
    return ANY_TYPE_OPS


def _logical_type(json_type: str, fmt: str | None) -> str:
    return "date" if json_type == "string" and fmt == "date" else json_type


# ---- validation -------------------------------------------------------


def validate_rules(rules: list[dict[str, Any]], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    paths = field_paths(schema)
    formats = field_formats(schema)
    arrays = array_paths(schema)
    seen_ids: set[str] = set()

    if not isinstance(rules, list):
        return ["rules must be a list"]

    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"rule[{i}]: must be an object")
            continue
        label = rule.get("id") or f"rule[{i}]"

        rule_id = rule.get("id")
        if not rule_id or not isinstance(rule_id, str):
            errors.append(f"{label}: missing or invalid 'id'")
        elif rule_id in seen_ids:
            errors.append(f"{label}: duplicate rule id")
        else:
            seen_ids.add(rule_id)

        if rule.get("category") not in CATEGORIES:
            errors.append(f"{label}: invalid category '{rule.get('category')}'")

        weight = rule.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0:
            errors.append(f"{label}: weight must be a non-negative number")

        condition = rule.get("condition")
        if not isinstance(condition, dict):
            errors.append(f"{label}: missing condition")
            continue
        errors.extend(_validate_condition(condition, paths, formats, arrays, label))

    return errors


def _validate_condition(
    cond: dict[str, Any],
    paths: dict[str, str],
    formats: dict[str, str | None],
    arrays: dict[str, dict[str, Any]],
    label: str,
) -> list[str]:
    errors: list[str] = []

    for combinator in ("all", "any"):
        if combinator in cond:
            children = cond[combinator]
            if not isinstance(children, list) or not children:
                return [f"{label}: '{combinator}' must be a non-empty list"]
            for child in children:
                if not isinstance(child, dict):
                    errors.append(f"{label}: '{combinator}' entries must be objects")
                    continue
                errors.extend(_validate_condition(child, paths, formats, arrays, label))
            return errors

    if "not" in cond:
        if not isinstance(cond["not"], dict):
            return [f"{label}: 'not' must be an object"]
        return _validate_condition(cond["not"], paths, formats, arrays, label)

    field = cond.get("field")
    op = cond.get("op")
    if not field or not op:
        return [f"{label}: condition missing 'field' or 'op'"]

    if op in ARRAY_OPS:
        if field not in arrays:
            return [f"{label}: '{field}' is not an array field"]
        if op in ("count_eq", "count_gte", "count_lte"):
            value = cond.get("value")
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{label}: '{op}' requires an integer 'value'")
        else:  # any_item / all_item
            item_cond = cond.get("item")
            if not isinstance(item_cond, dict):
                errors.append(f"{label}: '{op}' requires an 'item' condition")
            else:
                item_schema = arrays[field]
                item_paths = field_paths(item_schema)
                item_formats = field_formats(item_schema)
                errors.extend(_validate_condition(item_cond, item_paths, item_formats, {}, label))
        return errors

    if field not in paths:
        return [f"{label}: unknown field '{field}'"]

    logical_type = _logical_type(paths[field], formats.get(field))
    allowed_ops = _ops_for_logical_type(logical_type)
    if op not in allowed_ops:
        return [f"{label}: operator '{op}' is not valid for field '{field}' (type {logical_type})"]

    value = cond.get("value")
    if op in ("in", "not_in") and not isinstance(value, list):
        errors.append(f"{label}: '{op}' requires a list 'value'")
    elif op == "between" and not (isinstance(value, list) and len(value) == 2):
        errors.append(f"{label}: 'between' requires a 2-item list 'value'")
    elif op in ("within_days", "older_than_days") and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        errors.append(f"{label}: '{op}' requires an integer 'value'")
    elif op == "matches":
        if not isinstance(value, str) or not value or len(value) > MAX_PATTERN_LENGTH:
            errors.append(
                f"{label}: 'matches' requires a non-empty pattern up to {MAX_PATTERN_LENGTH} chars"
            )
        else:
            try:
                re2.compile(value)
            except re2.error as exc:
                errors.append(f"{label}: invalid regex pattern: {exc}")
    elif (
        op in ("eq", "neq", "gt", "gte", "lt", "lte", "contains", "before", "after")
        and value is None
    ):
        errors.append(f"{label}: '{op}' requires a 'value'")

    return errors


# ---- evaluation ---------------------------------------------------------


def _resolve(path: str, scope: Any) -> Any:
    current = scope
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _as_date(value: Any, evaluated_at: datetime) -> date | None:
    if value == "today":
        return evaluated_at.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _eval_condition(cond: dict[str, Any], scope: Any, evaluated_at: datetime) -> tuple[bool, Any]:
    for combinator, agg in (("all", all), ("any", any)):
        if combinator in cond:
            outcomes = [_eval_condition(c, scope, evaluated_at) for c in cond[combinator]]
            return agg(passed for passed, _ in outcomes), [actual for _, actual in outcomes]
    if "not" in cond:
        passed, actual = _eval_condition(cond["not"], scope, evaluated_at)
        return not passed, actual

    field = cond["field"]
    op = cond["op"]
    # validate_rules() already guarantees `value` is present and correctly
    # typed for every operator that needs one; cast(Any, ...) reflects that —
    # mypy otherwise keeps `| None` in the union through every comparison
    # below even though the dict's own value type is already Any.
    value: Any = cast(Any, cond.get("value"))

    if op in ARRAY_OPS:
        arr = _resolve(field, scope)
        if not isinstance(arr, list):
            return False, arr
        if op == "count_eq":
            return len(arr) == value, len(arr)
        if op == "count_gte":
            return len(arr) >= value, len(arr)
        if op == "count_lte":
            return len(arr) <= value, len(arr)
        if op in ("any_item", "all_item"):
            item_passed = [_eval_condition(cond["item"], item, evaluated_at)[0] for item in arr]
            if op == "any_item":
                return any(item_passed), len(arr)
            return len(item_passed) > 0 and all(item_passed), len(arr)

    resolved = _resolve(field, scope)
    actual = None if resolved is _MISSING else resolved

    if op == "exists":
        return resolved is not _MISSING and resolved is not None, actual
    if op == "not_exists":
        return resolved is _MISSING or resolved is None, actual
    if op == "is_empty":
        return actual in (None, "", []), actual

    if resolved is _MISSING:
        return False, None

    if op == "is_true":
        return actual is True, actual
    if op == "is_false":
        return actual is False, actual
    if op == "eq":
        return actual == value, actual
    if op == "neq":
        return actual != value, actual
    if op == "in":
        return actual in value, actual
    if op == "not_in":
        return actual not in value, actual
    if op == "contains":
        return isinstance(actual, str) and isinstance(value, str) and value in actual, actual
    if op == "matches":
        return bool(isinstance(actual, str) and re2.search(value, actual)), actual

    if op in ("gt", "gte", "lt", "lte", "between"):
        try:
            num_actual = Decimal(str(actual))
        except (InvalidOperation, ValueError):
            return False, actual
        try:
            if op == "between":
                lo, hi = value
                return Decimal(str(lo)) <= num_actual <= Decimal(str(hi)), actual
            num_value = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return False, actual
        if op == "gt":
            return num_actual > num_value, actual
        if op == "gte":
            return num_actual >= num_value, actual
        if op == "lt":
            return num_actual < num_value, actual
        if op == "lte":
            return num_actual <= num_value, actual

    if op in ("before", "after", "within_days", "older_than_days"):
        field_date = _as_date(actual, evaluated_at) if isinstance(actual, str) else None
        if field_date is None:
            return False, actual
        today = evaluated_at.date()
        if op == "before":
            compare = _as_date(value, evaluated_at)
            return compare is not None and field_date < compare, actual
        if op == "after":
            compare = _as_date(value, evaluated_at)
            return compare is not None and field_date > compare, actual
        if op == "within_days":
            return 0 <= (today - field_date).days <= value, actual
        if op == "older_than_days":
            return (today - field_date).days > value, actual

    return False, actual


def evaluate(
    rules: list[dict[str, Any]], data: dict[str, Any], evaluated_at: datetime
) -> list[RuleResult]:
    results: list[RuleResult] = []
    for rule in rules:
        try:
            passed, actual = _eval_condition(rule["condition"], data, evaluated_at)
        except Exception:
            passed, actual = False, None
        results.append(
            RuleResult(
                rule_id=rule["id"],
                category=rule["category"],
                passed=passed,
                message=rule.get("message", ""),
                actual=actual,
            )
        )
    return results
