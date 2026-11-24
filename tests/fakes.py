"""Offline fakes. No API key, no network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class FakeLLM:
    """Implements StructuredLLM by returning canned objects per schema."""

    def __init__(self, responses: dict[type, Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def extract(self, *, system: str, user: str, schema: type) -> Any:
        self.calls.append({"system": system, "user": user, "schema": schema})
        return self.responses[schema]


class FakeAnthropicClient:
    """Mimics the tiny slice of the SDK that ClaudeLLM touches."""

    def __init__(self, parsed_output: Any = None, stop_reason: str = "end_turn") -> None:
        self.kwargs: dict[str, Any] | None = None
        self._response = SimpleNamespace(
            parsed_output=parsed_output, stop_reason=stop_reason, stop_details=None
        )
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self._response
