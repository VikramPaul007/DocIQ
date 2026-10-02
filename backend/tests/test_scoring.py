from __future__ import annotations

from decimal import Decimal

from app.domain.rules import RuleResult
from app.domain.scoring import score, validate_scoring_config

RULES = [
    {"id": "has-pan", "category": "completeness"},
    {"id": "age-ok", "category": "eligibility"},
]

SCORING = {
    "weights": {"has-pan": 10, "age-ok": 30},
    "thresholds": {"low": 0, "medium": 10, "high": 30},
}


def test_validate_rejects_unknown_rule_reference() -> None:
    bad = {"weights": {"nope": 5}, "thresholds": {"low": 0, "medium": 1, "high": 2}}
    errors = validate_scoring_config(bad, RULES)
    assert any("unknown rule" in e for e in errors)


def test_validate_rejects_non_monotonic_thresholds() -> None:
    bad = {"weights": {}, "thresholds": {"low": 5, "medium": 1, "high": 2}}
    errors = validate_scoring_config(bad, RULES)
    assert any("thresholds" in e for e in errors)


def test_validate_accepts_well_formed_config() -> None:
    assert validate_scoring_config(SCORING, RULES) == []


def test_score_only_counts_failed_rules() -> None:
    results = [
        RuleResult(rule_id="has-pan", category="completeness", passed=True, message="", actual=None),
        RuleResult(rule_id="age-ok", category="eligibility", passed=False, message="too young", actual=15),
    ]
    result = score(results, SCORING)
    assert result.total == Decimal("30")
    assert result.risk_level == "high"
    assert result.contributors == [
        {"rule_id": "age-ok", "category": "eligibility", "weight": "30", "message": "too young"}
    ]


def test_score_thresholds_golden_cases() -> None:
    def make(total_weight_rule_ids):
        return [
            RuleResult(rule_id=rid, category="x", passed=rid not in total_weight_rule_ids, message="", actual=None)
            for rid in ("has-pan", "age-ok")
        ]

    assert score(make([]), SCORING).risk_level == "low"  # nothing failed -> 0
    assert score(make(["has-pan"]), SCORING).risk_level == "medium"  # 10 -> medium
    assert score(make(["has-pan", "age-ok"]), SCORING).risk_level == "high"  # 40 -> high


def test_score_ignores_zero_weight_rules_as_contributors() -> None:
    scoring = {"weights": {"has-pan": 0, "age-ok": 10}, "thresholds": {"low": 0, "medium": 5, "high": 10}}
    results = [
        RuleResult(rule_id="has-pan", category="completeness", passed=False, message="", actual=None),
        RuleResult(rule_id="age-ok", category="eligibility", passed=False, message="", actual=None),
    ]
    result = score(results, scoring)
    assert len(result.contributors) == 1
    assert result.contributors[0]["rule_id"] == "age-ok"
