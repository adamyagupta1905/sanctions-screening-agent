"""Record live model outputs once, replay them for free.

Key = sha256(system prompt + user prompt + schema name). If the prompt, the case data or
the redaction changes, the key changes and replay fails loudly (CassetteMiss) instead of
scoring a stale answer against a new question.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from screening.llm import LLMIncomplete, LLMRefusal

_ERRORS = {"LLMRefusal": LLMRefusal, "LLMIncomplete": LLMIncomplete}


class CassetteMiss(KeyError):
    """No recording for this exact prompt."""


def prompt_key(system: str, user: str, schema: type[BaseModel]) -> str:
    return hashlib.sha256(f"{system}\n---\n{user}\n---\n{schema.__name__}".encode()).hexdigest()


class RecordingLLM:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model_name = getattr(inner, "model_name", type(inner).__name__)
        self.records: dict[str, dict] = {}
        self.calls = 0

    def extract(self, *, system: str, user: str, schema: type[BaseModel]):
        key = prompt_key(system, user, schema)
        self.calls += 1
        meta = {"model": self.model_name, "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            out = self.inner.extract(system=system, user=user, schema=schema)
        except (LLMRefusal, LLMIncomplete) as exc:
            self.records[key] = {**meta, "error_type": type(exc).__name__, "error": str(exc)}
            raise
        self.records[key] = {**meta, "output": json.loads(out.model_dump_json())}
        return out

    def save(self, path: Path) -> None:
        if not self.records:
            return  # regression: a run that failed before any response wrote an empty cassette
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(path.read_text()) if path.exists() else {}
        existing.update(self.records)
        path.write_text(json.dumps(existing, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


class ReplayLLM:
    def __init__(self, path: Path) -> None:
        self.records: dict[str, dict] = json.loads(path.read_text())
        models = {r["model"] for r in self.records.values()}
        self.model_name = "replay:" + ",".join(sorted(models))
        self.calls = 0

    def extract(self, *, system: str, user: str, schema: type[BaseModel]):
        self.calls += 1
        key = prompt_key(system, user, schema)
        if key not in self.records:
            raise CassetteMiss(f"no recording for prompt {key[:12]}; re-record with --live")
        rec = self.records[key]
        if "error_type" in rec:
            raise _ERRORS[rec["error_type"]](rec["error"])
        return schema.model_validate(rec["output"])
