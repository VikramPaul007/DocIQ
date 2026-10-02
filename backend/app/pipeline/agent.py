"""Template Agent: turns a name/purpose/document_type into a first-draft
system prompt + JSON Schema. The provider already repairs structurally
invalid JSON once (see providers/llm.py); this adds a second, domain-specific
repair pass for violations of our JSON Schema *subset* (additionalProperties,
unsupported types, $ref, depth/field caps — things a generic LLM provider
has no reason to know about).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain import schema_subset
from app.providers.llm import LlmProvider

AGENT_SYSTEM_PROMPT = """You are a Template Agent for a document intelligence platform.
Given a business user's description of a document type and what should be
extracted from it, produce:
1. A system prompt that a separate extraction model will use, instructing it
   to extract the described fields from OCR text of a document.
2. A JSON Schema (object type) describing exactly those fields.

Schema rules you MUST follow:
- Root type is "object" with "additionalProperties": false.
- Only use types: string, number, integer, boolean, object, array.
- Dates must be {"type": "string", "format": "date"}.
- Every object (including the root) must set "additionalProperties": false
  and list its own "properties".
- Keep nesting to at most 5 levels and fields to at most 200.
- Add a "description" to each field explaining what to extract.
- Mark fields that are clearly mandatory in "required".

Respond with a JSON object with exactly two keys: "system_prompt" (string)
and "json_schema" (object, per the rules above)."""

AGENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["system_prompt", "json_schema"],
    "properties": {
        "system_prompt": {"type": "string"},
        "json_schema": {"type": "object"},
    },
}


@dataclass
class AgentResult:
    system_prompt: str
    json_schema: dict[str, Any]
    model: str


class AgentGenerationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _user_message(name: str, purpose: str, document_type: str) -> str:
    return (
        f"Template name: {name}\n"
        f"Document type: {document_type}\n"
        f"Purpose / what to extract: {purpose}\n"
    )


def generate(provider: LlmProvider, *, name: str, purpose: str, document_type: str) -> AgentResult:
    user_message = _user_message(name, purpose, document_type)
    result = provider.complete_json(
        system=AGENT_SYSTEM_PROMPT, user=user_message, schema=AGENT_RESPONSE_SCHEMA
    )

    schema_errors = schema_subset.validate(result.content.get("json_schema", {}))
    if schema_errors:
        repair_user = (
            f"{user_message}\nYour previous json_schema had these problems — fix them and "
            f"resend the full response with both keys:\n- " + "\n- ".join(schema_errors)
        )
        result = provider.complete_json(
            system=AGENT_SYSTEM_PROMPT, user=repair_user, schema=AGENT_RESPONSE_SCHEMA
        )
        schema_errors = schema_subset.validate(result.content.get("json_schema", {}))
        if schema_errors:
            raise AgentGenerationError(schema_errors)

    return AgentResult(
        system_prompt=result.content["system_prompt"],
        json_schema=result.content["json_schema"],
        model=result.model,
    )
