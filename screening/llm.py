"""The ONLY module that imports a model SDK (Anthropic, Google Gen AI).

Everything else depends on the `StructuredLLM` protocol, so tests and the
offline eval can swap in a fake with no API key and no network.

Business role: the LLM reads evidence and writes structured text (summaries,
case notes). It never scores matches, never decides a disposition and never
does arithmetic; those live in plain Python elsewhere.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

import anthropic
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "claude-opus-5-5"
# Stable Gemini Flash model per Google's models page (checked 2026-11-24).
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

T = TypeVar("T", bound=BaseModel)


class LLMRefusal(RuntimeError):
    """The model declined the request (stop_reason == "refusal")."""


class LLMIncomplete(RuntimeError):
    """The model stopped before producing a valid structured answer."""


class StructuredLLM(Protocol):
    """Anything that turns (system, user text) into a validated Pydantic object."""

    def extract(self, *, system: str, user: str, schema: type[T]) -> T: ...


class ClaudeLLM:
    """Production adapter. The SDK client is injected so tests can pass a fake."""

    def __init__(
        self,
        client: Any | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 16000,
        effort: str = "medium",
    ) -> None:
        # anthropic.Anthropic() resolves credentials from the environment
        # (ANTHROPIC_API_KEY or `ant auth login`). Never hard-code a key.
        self._client = client if client is not None else anthropic.Anthropic()
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self.usage: list[dict[str, int]] = []  # one entry per call, for cost reporting

    @property
    def model_name(self) -> str:
        return self._model

    def extract(self, *, system: str, user: str, schema: type[T]) -> T:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": self._effort},
        )
        u = getattr(response, "usage", None)
        if u is not None:
            self.usage.append({"input_tokens": getattr(u, "input_tokens", 0) or 0,
                               "output_tokens": getattr(u, "output_tokens", 0) or 0})
        if response.stop_reason == "refusal":
            raise LLMRefusal(str(getattr(response, "stop_details", None)))
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMIncomplete(f"stop_reason={response.stop_reason}")
        return response.parsed_output


# Gemini finish reasons that mean "the provider declined", mapped to LLMRefusal.
_GEMINI_DECLINED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE"}


class GeminiLLM:
    """Google Gemini adapter with the same contract as ClaudeLLM.

    Structured output: the Pydantic schema is sent as `response_json_schema`, and the
    returned text is validated by Pydantic here, so both providers are held to exactly
    the same schema check. The client is injected so tests can pass a fake.
    """

    def __init__(self, client: Any | None = None, model: str = DEFAULT_GEMINI_MODEL,
                 max_output_tokens: int = 16000) -> None:
        # genai.Client() reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment.
        self._client = client if client is not None else genai.Client()
        self._model = model
        self._max_output_tokens = max_output_tokens
        self.usage: list[dict[str, int]] = []

    @property
    def model_name(self) -> str:
        return self._model

    def extract(self, *, system: str, user: str, schema: type[T]) -> T:
        response = self._client.models.generate_content(
            model=self._model,
            contents=user,
            config=genai_types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
                max_output_tokens=self._max_output_tokens,
            ),
        )
        u = getattr(response, "usage_metadata", None)
        if u is not None:
            # Thinking tokens are billed as output, so they are counted as output here.
            self.usage.append({
                "input_tokens": getattr(u, "prompt_token_count", 0) or 0,
                "output_tokens": (getattr(u, "candidates_token_count", 0) or 0)
                + (getattr(u, "thoughts_token_count", 0) or 0),
            })
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise LLMRefusal(f"prompt blocked: {feedback.block_reason}")
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise LLMIncomplete("no candidates returned")
        finish = candidates[0].finish_reason
        finish_name = getattr(finish, "name", str(finish))
        if finish_name in _GEMINI_DECLINED:
            raise LLMRefusal(f"finish_reason={finish_name}")
        if finish_name == "MAX_TOKENS":
            raise LLMIncomplete("finish_reason=MAX_TOKENS")
        try:
            return schema.model_validate_json(response.text or "")
        except ValidationError as exc:
            raise LLMIncomplete(f"output did not match schema: {exc.error_count()} errors") from exc
