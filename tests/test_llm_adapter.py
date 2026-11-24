import ast
from pathlib import Path

import pytest

from screening.llm import DEFAULT_MODEL, ClaudeLLM, LLMIncomplete, LLMRefusal
from screening.models import CaseNote, EvidenceClaim
from tests.fakes import FakeAnthropicClient, FakeLLM

NOTE = CaseNote(
    summary="Name similarity only; DOB differs.",
    claims=[EvidenceClaim(statement="DOB mismatch", evidence_ids=["cust-1", "sdn-42"])],
    recommendation="Likely false positive; reviewer to confirm.",
)


def test_claude_adapter_returns_parsed_output_and_sends_schema():
    client = FakeAnthropicClient(parsed_output=NOTE)
    llm = ClaudeLLM(client=client)
    out = llm.extract(system="s", user="u", schema=CaseNote)
    assert out is NOTE
    assert client.kwargs["model"] == DEFAULT_MODEL
    assert client.kwargs["output_format"] is CaseNote
    assert client.kwargs["system"] == "s"


def test_refusal_raises_instead_of_returning_empty():
    llm = ClaudeLLM(client=FakeAnthropicClient(stop_reason="refusal"))
    with pytest.raises(LLMRefusal):
        llm.extract(system="s", user="u", schema=CaseNote)


@pytest.mark.parametrize("stop_reason", ["max_tokens", "end_turn"])
def test_missing_output_raises(stop_reason):
    llm = ClaudeLLM(client=FakeAnthropicClient(parsed_output=None, stop_reason=stop_reason))
    with pytest.raises(LLMIncomplete):
        llm.extract(system="s", user="u", schema=CaseNote)


def test_fake_llm_records_calls():
    fake = FakeLLM({CaseNote: NOTE})
    assert fake.extract(system="s", user="u", schema=CaseNote) is NOTE
    assert fake.calls[0]["schema"] is CaseNote


def test_only_llm_module_imports_the_sdk():
    """Invariant: the model SDK is isolated behind screening/llm.py."""
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        if ".venv" in path.parts or path.name == "llm.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.split(".")[0] == "anthropic" for n in names):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
