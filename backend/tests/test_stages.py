"""Unit tests for the pipeline stage functions — plain in-memory Record
objects (never added to a session), Fake LLM/OCR providers, no DB or network.
"""

from __future__ import annotations

import pytest

from app.models import Record, TemplateVersion
from app.pipeline import stages
from app.providers.llm import FakeLlmProvider, LlmOutputInvalidError
from app.providers.ocr import FakeOcrProvider
from tests.conftest import minimal_blank_pdf, minimal_pdf_with_text

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "properties": {"name": {"type": "string"}},
}


def _version(**overrides) -> TemplateVersion:
    defaults = {"system_prompt": "Extract the name.", "json_schema": SCHEMA, "rules": [], "scoring": {}}
    defaults.update(overrides)
    return TemplateVersion(**defaults)


def test_run_ocr_uses_text_layer_when_present() -> None:
    record = Record()
    stages.run_ocr(record, minimal_pdf_with_text("Hello World Testing 123"), FakeOcrProvider())
    assert record.ocr["pages"][0]["method"] == "text_layer"
    assert "Hello World Testing 123" in record.ocr["pages"][0]["text"]


def test_run_ocr_falls_back_to_ocr_provider_for_blank_page() -> None:
    record = Record()
    stages.run_ocr(record, minimal_blank_pdf(), FakeOcrProvider(text="ocr'd text", confidence=0.9))
    page = record.ocr["pages"][0]
    assert page["method"] == "ocr"
    assert page["text"] == "ocr'd text"
    assert page["confidence"] == 0.9


def test_run_ocr_raises_permanent_error_for_corrupt_pdf() -> None:
    record = Record()
    with pytest.raises(stages.PermanentStageError):
        stages.run_ocr(record, b"not a pdf at all", FakeOcrProvider())


def test_run_extraction_populates_record_on_success() -> None:
    record = Record(ocr={"pages": [{"page": 1, "method": "text_layer", "text": "Name: Alice", "confidence": None}]})
    llm = FakeLlmProvider([{"name": "Alice"}])
    stages.run_extraction(record, _version(), llm)
    assert record.extraction == {"name": "Alice"}
    assert record.model_meta["model"] == "fake"


def test_run_extraction_marks_invalid_without_raising() -> None:
    record = Record(ocr={"pages": []})

    class AlwaysInvalidProvider:
        def complete_json(self, **kwargs):
            raise LlmOutputInvalidError(["bad json"], "raw")

    stages.run_extraction(record, _version(), AlwaysInvalidProvider())
    assert record.extraction_valid is False
    assert record.extraction == {}


def test_run_extraction_raises_transient_on_provider_error() -> None:
    record = Record(ocr={"pages": []})

    class FlakyProvider:
        def complete_json(self, **kwargs):
            raise ConnectionError("boom")

    with pytest.raises(stages.TransientStageError):
        stages.run_extraction(record, _version(), FlakyProvider())


def test_run_validation_flags_schema_violations() -> None:
    record = Record(extraction={"wrong_field": 1})
    stages.run_validation(record, _version())
    assert record.extraction_valid is False
    assert record.validation_errors


def test_run_validation_passes_conforming_extraction() -> None:
    record = Record(extraction={"name": "Alice"})
    stages.run_validation(record, _version())
    assert record.extraction_valid is True
    assert record.validation_errors == []


def test_run_rules_and_scoring_end_to_end() -> None:
    rules = [
        {
            "id": "has-name",
            "category": "completeness",
            "name": "Name present",
            "message": "Name is required",
            "weight": 10,
            "condition": {"field": "name", "op": "exists"},
        }
    ]
    scoring = {"weights": {"has-name": 10}, "thresholds": {"low": 0, "medium": 5, "high": 20}}
    record = Record(extraction={})  # name missing -> rule fails -> scored
    version = _version(rules=rules, scoring=scoring)

    stages.run_rules(record, version)
    assert record.rule_results[0]["passed"] is False
    assert record.evaluated_at is not None

    stages.run_scoring(record, version)
    assert record.score == 10
    assert record.risk_level == "medium"
    assert record.contributors[0]["rule_id"] == "has-name"


def test_run_summary_succeeds() -> None:
    record = Record(extraction={"name": "Alice"})
    stages.run_summary(record, FakeLlmProvider([{}], text_response="A short summary."))
    assert record.summary == "A short summary."


def test_run_summary_is_non_blocking_on_repeated_failure() -> None:
    record = Record(extraction={"name": "Alice"})

    class AlwaysFailsProvider:
        def complete_json(self, **kwargs):
            raise AssertionError("not used")

        def complete_text(self, **kwargs):
            raise ConnectionError("boom")

    stages.run_summary(record, AlwaysFailsProvider())
    assert record.summary is None
