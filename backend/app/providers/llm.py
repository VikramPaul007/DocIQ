"""LLM provider abstraction: one call that returns schema-validated JSON.

``OpenAICompatibleProvider`` works with anything serving an OpenAI-style chat
completions API (OpenAI, Azure OpenAI, vLLM, Ollama, LM Studio...) via
base_url + api_key from config. Not every such backend supports strict
``json_schema`` response mode, so it's tried first and ``json_object`` mode is
the fallback. Output is always re-validated against the caller's schema
regardless of which mode produced it; one repair retry is attempted before
giving up.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import jsonschema
from openai import OpenAI

from app.config import settings

# Input is truncated to this many characters (a generous multi-page budget)
# before being sent — OCR text for a 50-page document can otherwise blow past
# most context windows. Callers that truncate should record it in model_meta.
MAX_INPUT_CHARS = 60_000


@dataclass
class LlmResult:
    content: dict[str, Any]
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float


class LlmOutputInvalidError(ValueError):
    def __init__(self, errors: list[str], raw_text: str):
        self.errors = errors
        self.raw_text = raw_text
        super().__init__("; ".join(errors))


class LlmProvider(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2000,
        timeout: float = 30.0,
    ) -> LlmResult: ...


def _parse_and_validate(text: str, schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    try:
        content = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, [f"Response was not valid JSON: {exc}"]
    validator = jsonschema.Draft202012Validator(schema)
    errors = [e.message for e in validator.iter_errors(content)]
    return content, errors


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._client = OpenAI(
            base_url=base_url or settings.llm_base_url, api_key=api_key or settings.llm_api_key
        )
        self._model = model or settings.llm_model

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2000,
        timeout: float = 30.0,
    ) -> LlmResult:
        user = user[:MAX_INPUT_CHARS]
        start = time.monotonic()
        text, usage = self._call(system, user, schema, max_tokens, timeout)
        content, errors = _parse_and_validate(text, schema)

        if errors:
            repair_user = (
                f"{user}\n\nYour previous response was invalid JSON or didn't match the "
                f"required schema. Errors: {errors}. Respond again with corrected JSON only."
            )
            text, usage = self._call(system, repair_user, schema, max_tokens, timeout)
            content, errors = _parse_and_validate(text, schema)
            if errors:
                raise LlmOutputInvalidError(errors, text)

        latency_ms = (time.monotonic() - start) * 1000
        return LlmResult(
            content=content,
            model=self._model,
            prompt_tokens=usage.get("prompt_tokens") if usage else None,
            completion_tokens=usage.get("completion_tokens") if usage else None,
            latency_ms=latency_ms,
        )

    def _call(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int, timeout: float
    ) -> tuple[str, dict[str, Any] | None]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # The SDK's overloads type `model` as a huge Literal union of OpenAI's
        # own model names and `messages`/`response_format` very narrowly, which
        # doesn't fit an OpenAI-*compatible* provider taking an arbitrary
        # configured model string and a dict built at runtime.
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                timeout=timeout,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": schema, "strict": True},
                },
            )  # type: ignore[call-overload]
        except Exception:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[*messages, {"role": "system", "content": "Respond with JSON only."}],
                max_tokens=max_tokens,
                timeout=timeout,
                response_format={"type": "json_object"},
            )  # type: ignore[call-overload]
        text = resp.choices[0].message.content or ""
        usage = resp.usage.model_dump() if resp.usage else None
        return text, usage


class FakeLlmProvider:
    """Test double: returns each entry in ``responses`` in sequence (the last
    one repeats if called more times than provided), so tests can exercise
    repair-retry loops deterministically without hitting a real LLM."""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = responses
        self.calls = 0

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2000,
        timeout: float = 30.0,
    ) -> LlmResult:
        content = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return LlmResult(
            content=content, model="fake", prompt_tokens=10, completion_tokens=10, latency_ms=1.0
        )
