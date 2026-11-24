"""GeminiLLM against a fake client. No API key, no network."""

from types import SimpleNamespace

import pytest

from screening.llm import DEFAULT_GEMINI_MODEL, GeminiLLM, LLMIncomplete, LLMRefusal
from screening.models import CaseNote, EvidenceClaim

def reason(name):  # stand-in for the SDK's FinishReason enum (tests must not import the SDK)
    return SimpleNamespace(name=name)


NOTE = CaseNote(summary="s", claims=[EvidenceClaim(statement="x", evidence_ids=["M1"])], recommendation="r")


class FakeGenai:
    def __init__(self, text=NOTE.model_dump_json(), finish=reason("STOP"), block=None, candidates=True,
                 usage=(1000, 300, 500)):
        self.kwargs = None
        self._resp = SimpleNamespace(
            text=text,
            candidates=[SimpleNamespace(finish_reason=finish)] if candidates else [],
            prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
            usage_metadata=SimpleNamespace(prompt_token_count=usage[0], candidates_token_count=usage[1],
                                           thoughts_token_count=usage[2]),
        )
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kwargs):
        self.kwargs = kwargs
        return self._resp


def test_returns_validated_schema_object_and_sends_config():
    fake = FakeGenai()
    llm = GeminiLLM(client=fake)
    assert llm.extract(system="sys", user="u", schema=CaseNote) == NOTE
    cfg = fake.kwargs["config"]
    assert fake.kwargs["model"] == DEFAULT_GEMINI_MODEL and fake.kwargs["contents"] == "u"
    assert cfg.system_instruction == "sys" and cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == CaseNote.model_json_schema()


def test_usage_counts_thinking_tokens_as_output():
    llm = GeminiLLM(client=FakeGenai(usage=(1000, 300, 500)))
    llm.extract(system="s", user="u", schema=CaseNote)
    assert llm.usage == [{"input_tokens": 1000, "output_tokens": 800}]


@pytest.mark.parametrize("finish", [reason("SAFETY"), reason("PROHIBITED_CONTENT")])
def test_safety_stop_is_a_refusal(finish):
    with pytest.raises(LLMRefusal):
        GeminiLLM(client=FakeGenai(finish=finish)).extract(system="s", user="u", schema=CaseNote)


def test_blocked_prompt_is_a_refusal():
    with pytest.raises(LLMRefusal, match="blocked"):
        GeminiLLM(client=FakeGenai(block="SAFETY")).extract(system="s", user="u", schema=CaseNote)


@pytest.mark.parametrize("kwargs", [
    {"finish": reason("MAX_TOKENS")},
    {"text": '{"summary": "only a summary"}'},  # schema violation: claims missing
    {"text": "not json"},
    {"text": None},
    {"candidates": False},
])
def test_incomplete_or_invalid_output(kwargs):
    with pytest.raises(LLMIncomplete):
        GeminiLLM(client=FakeGenai(**kwargs)).extract(system="s", user="u", schema=CaseNote)


def test_schema_constraints_enforced_locally():
    """A claim without evidence ids violates CaseNote even if the provider ignores minItems."""
    bad = '{"summary":"s","claims":[{"statement":"x","evidence_ids":[]}],"recommendation":"r"}'
    with pytest.raises(LLMIncomplete):
        GeminiLLM(client=FakeGenai(text=bad)).extract(system="s", user="u", schema=CaseNote)
