"""The ONLY module that imports the Anthropic SDK.

Everything else depends on the `StructuredLLM` protocol, so tests and the
offline eval can swap in a fake with no API key and no network.

Business role: the LLM reads evidence and writes structured text (summaries,
case notes). It never scores matches, never decides a disposition and never
does arithmetic; those live in plain Python elsewhere.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-5-5"

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

    def extract(self, *, system: str, user: str, schema: type[T]) -> T:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": self._effort},
        )
        if response.stop_reason == "refusal":
            raise LLMRefusal(str(getattr(response, "stop_details", None)))
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMIncomplete(f"stop_reason={response.stop_reason}")
        return response.parsed_output
