"""Scoring: the sum of the weights of the rules that *failed*, compared
against configurable thresholds to produce a risk level, with the
contributing rules persisted for explainability (see "Scoring Engine Design"
in docs/technical-implementation-plan.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.domain.rules import RuleResult

RISK_LEVELS = ("low", "medium", "high")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class ScoringResult:
    total: Decimal
    risk_level: str
    contributors: list[dict[str, Any]]


def validate_scoring_config(scoring: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
    if not scoring:
        return []  # scoring is optional: an unscored template always scores 0/low
    if not isinstance(scoring, dict):
        return ["scoring must be an object"]

    errors: list[str] = []
    rule_ids = {r.get("id") for r in rules if isinstance(r, dict)}
    weights = scoring.get("weights", {})
    if not isinstance(weights, dict):
        errors.append("scoring.weights must be an object")
    else:
        for rule_id, weight in weights.items():
            if rule_id not in rule_ids:
                errors.append(f"scoring.weights references unknown rule '{rule_id}'")
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0:
                errors.append(f"scoring.weights['{rule_id}'] must be a non-negative number")

    thresholds = scoring.get("thresholds", {})
    if not isinstance(thresholds, dict) or not all(k in thresholds for k in RISK_LEVELS):
        errors.append("scoring.thresholds must define low, medium and high")
    else:
        low, medium, high = (thresholds.get(k) for k in RISK_LEVELS)
        if not all(_is_number(v) for v in (low, medium, high)):
            errors.append("scoring.thresholds values must be numbers")
        else:
            low_d, medium_d, high_d = Decimal(str(low)), Decimal(str(medium)), Decimal(str(high))
            if not (0 <= low_d < medium_d < high_d):
                errors.append("scoring.thresholds must satisfy 0 <= low < medium < high")

    return errors


def score(results: list[RuleResult], scoring: dict[str, Any]) -> ScoringResult:
    """Only ever reads persisted rule results and the version's own scoring
    config — never re-derives anything from raw extracted data, so a replay
    of an old record with its original template version is always identical."""
    weights: dict[str, Any] = scoring.get("weights", {})
    thresholds: dict[str, Any] = scoring.get("thresholds", {"low": 0, "medium": 0, "high": 0})

    contributors: list[dict[str, Any]] = []
    total = Decimal("0")
    for result in results:
        if result.passed:
            continue
        weight = Decimal(str(weights.get(result.rule_id, 0)))
        if weight > 0:
            total += weight
            contributors.append(
                {
                    "rule_id": result.rule_id,
                    "category": result.category,
                    "weight": str(weight),
                    "message": result.message,
                }
            )

    medium_t = Decimal(str(thresholds.get("medium", 0)))
    high_t = Decimal(str(thresholds.get("high", 0)))
    if total >= high_t:
        risk_level = "high"
    elif total >= medium_t:
        risk_level = "medium"
    else:
        risk_level = "low"

    contributors.sort(key=lambda c: Decimal(c["weight"]), reverse=True)
    return ScoringResult(total=total, risk_level=risk_level, contributors=contributors)
