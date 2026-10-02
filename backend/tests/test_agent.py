from __future__ import annotations

import pytest

from app.pipeline.agent import AgentGenerationError, generate
from app.providers.llm import FakeLlmProvider

VALID_RESPONSE = {
    "system_prompt": "Extract the name.",
    "json_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"name": {"type": "string"}},
    },
}

# Missing additionalProperties: false — violates our schema subset even
# though it's a perfectly ordinary JSON Schema.
INVALID_SUBSET_RESPONSE = {
    "system_prompt": "Extract the name.",
    "json_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
}


def test_generate_succeeds_on_first_try() -> None:
    provider = FakeLlmProvider([VALID_RESPONSE])
    result = generate(provider, name="Onboarding", purpose="extract name", document_type="pdf")
    assert result.system_prompt == "Extract the name."
    assert result.json_schema["properties"]["name"]["type"] == "string"
    assert provider.calls == 1


def test_generate_repairs_schema_subset_violation_once() -> None:
    provider = FakeLlmProvider([INVALID_SUBSET_RESPONSE, VALID_RESPONSE])
    result = generate(provider, name="Onboarding", purpose="extract name", document_type="pdf")
    assert provider.calls == 2
    assert result.json_schema["additionalProperties"] is False


def test_generate_raises_after_repair_still_invalid() -> None:
    provider = FakeLlmProvider([INVALID_SUBSET_RESPONSE, INVALID_SUBSET_RESPONSE])
    with pytest.raises(AgentGenerationError):
        generate(provider, name="Onboarding", purpose="extract name", document_type="pdf")
    assert provider.calls == 2
