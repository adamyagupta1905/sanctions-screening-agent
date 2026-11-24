"""Workflow tests: every path through the graph, with a scripted fake LLM. No API key."""

from datetime import date

import pytest

from screening.case_note import PROMPT_VERSION, check_case_note
from screening.context import CustomerDocument, build_case_context
from screening.llm import LLMRefusal
from screening.matching import Matcher
from screening.models import CaseNote, Customer, EvidenceClaim, SanctionsEntry
from screening.workflow import build_workflow, pending_review, start_case, submit_decision
from tests.fakes import ScriptedLLM

SALT = b"test-salt-0123456789"
ENTRY = SanctionsEntry(entry_id="42", primary_name="QUILLFEATHER, Marten", entity_type="individual",
                       dates_of_birth=["14 Mar 1968"], nationalities=["Freedonia"])
ENTRIES = {"42": ENTRY}
HIT = Customer(customer_id="C1", full_name="Marten Quillfeather", date_of_birth=date(1990, 5, 5))
CLEAN = Customer(customer_id="C2", full_name="Olivia Hartley")

GOOD = CaseNote(
    summary="Name matches M1 exactly (score 1.0); DOB differs by 22 years.",
    claims=[EvidenceClaim(statement="Exact name match on the primary name.", evidence_ids=["M1", "CUST"]),
            EvidenceClaim(statement="Listed DOB is 22 years from the customer's.", evidence_ids=["M1"])],
    open_questions=["Confirm the customer's identity document."],
    recommendation="Check the DOB evidence first; it points away from a true match.",
)
BAD_ID = GOOD.model_copy(update={"claims": [EvidenceClaim(statement="x", evidence_ids=["M9"])]})
BAD_NUMBER = GOOD.model_copy(update={"summary": "Name score 0.97 against M1."})
DECIDES = GOOD.model_copy(update={"recommendation": "The case has been cleared."})
DECISION = {"disposition": "cleared", "reviewer": "analyst-7", "reason": "DOB 22 years apart, other nationality."}


def app_with(script):
    llm = ScriptedLLM(script)
    return build_workflow(Matcher(list(ENTRIES.values())), ENTRIES, llm, SALT), llm


def steps(state):
    return [e["step"] for e in state["events"]]


# --- the case-note checks ---------------------------------------------------------------

def _ctx():
    from screening.models import MatchCandidate, ScreeningResult
    result = ScreeningResult(customer_id="C1", alert=True, threshold=0.88, candidates=[
        MatchCandidate(customer_id="C1", entry_id="42", score=1.0, matched_on="QUILLFEATHER, Marten",
                       name_source="primary", dob_check="mismatch", dob_years_apart=22,
                       nationality_check="unknown")])
    return build_case_context(HIT, result, ENTRIES, [], SALT)[0]


def test_good_note_passes_checks():
    assert check_case_note(GOOD, _ctx()) == []


@pytest.mark.parametrize("note,fragment", [
    (BAD_ID, "do not exist"),
    (BAD_NUMBER, "0.97"),
    (GOOD.model_copy(update={"summary": "DOB differs by 30 years."}), "30 years"),
    (DECIDES, "decided"),
    (GOOD.model_copy(update={"claims": []}), "no cited claims"),
])
def test_bad_notes_rejected_with_reason(note, fragment):
    problems = check_case_note(note, _ctx())
    assert any(fragment in p for p in problems), problems


# --- graph paths ------------------------------------------------------------------------

def test_no_alert_ends_without_calling_the_model():
    app, llm = app_with([])
    state = start_case(app, "t-clean", CLEAN)
    assert state["status"] == "no_alert" and steps(state) == ["screen"]
    assert llm.calls == [] and pending_review(app, "t-clean") is None


def test_happy_path_pauses_for_human_then_records_decision():
    app, llm = app_with([GOOD])
    state = start_case(app, "t1", HIT, [CustomerDocument(source="kyc_note", text="Email a@b.test")])
    assert state["status"] == "alert" and "decision" not in state
    payload = pending_review(app, "t1")
    assert payload["note"]["summary"] == GOOD.summary and payload["candidates"][0]["dob_check"] == "mismatch"

    final = submit_decision(app, "t1", DECISION)
    assert final["status"] == "cleared"
    assert steps(final) == ["screen", "build_context", "draft_note", "human_review", "record"]
    assert pending_review(app, "t1") is None
    assert len(llm.calls) == 1


def test_model_never_sees_raw_customer_data():
    app, llm = app_with([GOOD])
    start_case(app, "t2", HIT, [CustomerDocument(source="kyc_note", text="Email a@b.test, born 1990-05-05")])
    sent = llm.calls[0]["system"] + llm.calls[0]["user"]
    for secret in ["C1\"", "1990", "a@b.test"]:
        assert secret not in sent


def test_failed_checks_retry_once_with_problems_listed():
    app, llm = app_with([BAD_NUMBER, GOOD])
    state = start_case(app, "t3", HIT)
    assert len(llm.calls) == 2 and "rejected by automated checks" in llm.calls[1]["user"]
    assert "0.97" in llm.calls[1]["user"]
    assert pending_review(app, "t3")["note"] is not None
    assert [e["passed_checks"] for e in state["events"] if e["step"] == "draft_note"] == [False, True]


def test_note_dropped_after_two_failures_but_case_still_reaches_human():
    app, _ = app_with([BAD_ID, DECIDES])
    start_case(app, "t4", HIT)
    payload = pending_review(app, "t4")
    assert payload["note"] is None and payload["note_problems"]
    assert submit_decision(app, "t4", DECISION)["status"] == "cleared"


def test_refusal_is_handled_like_a_failed_note():
    app, _ = app_with([LLMRefusal("declined"), GOOD])
    start_case(app, "t5", HIT)
    assert pending_review(app, "t5")["note"]["summary"] == GOOD.summary


@pytest.mark.parametrize("bad", [
    {"disposition": "pending", "reviewer": "a", "reason": "not decided yet at all"},
    {"disposition": "cleared", "reviewer": "", "reason": "valid reason text here"},
    {"disposition": "cleared", "reviewer": "a", "reason": "ok"},
    {"disposition": "deleted", "reviewer": "a", "reason": "valid reason text here"},
])
def test_invalid_decisions_rejected_and_case_stays_paused(bad):
    app, _ = app_with([GOOD])
    start_case(app, "t6", HIT)
    with pytest.raises(Exception):
        submit_decision(app, "t6", bad)
    assert pending_review(app, "t6") is not None  # still waiting for a valid decision
    assert submit_decision(app, "t6", DECISION)["status"] == "cleared"


def test_invalid_resume_bypassing_the_helper_pauses_again_instead_of_sticking():
    """Regression: a bad resume value crashed the node, LangGraph replayed it on every later
    resume, and the case could never be finished."""
    from langgraph.types import Command
    app, _ = app_with([GOOD])
    start_case(app, "t8", HIT)
    cfg = {"configurable": {"thread_id": "t8"}}
    app.invoke(Command(resume={"disposition": "cleared", "reviewer": "a", "reason": "ok"}), cfg)
    payload = pending_review(app, "t8")
    assert payload is not None and "reason" in payload["decision_error"]
    assert app.invoke(Command(resume=DECISION), cfg)["status"] == "cleared"


def test_events_carry_audit_fields():
    app, _ = app_with([GOOD])
    start_case(app, "t7", HIT)
    final = submit_decision(app, "t7", {**DECISION, "disposition": "escalated"})
    note_event = next(e for e in final["events"] if e["step"] == "draft_note")
    assert note_event["model"] == "scripted-fake" and note_event["prompt_version"] == PROMPT_VERSION
    assert len(note_event["prompt_sha256"]) == 64
    assert final["events"][-1] == {**final["events"][-1], "disposition": "escalated", "reviewer": "analyst-7"}
    assert all("at" in e for e in final["events"])


# --- regressions from the live Gemini run (2026-11-24): good notes wrongly rejected -----

def _multi_ctx(doc_text=None):
    from screening.models import MatchCandidate, ScreeningResult
    cands = [MatchCandidate(customer_id="C1", entry_id="42", score=1.0, matched_on="QUILLFEATHER, Marten",
                            name_source="primary", dob_check="exact", nationality_check="match")]
    entries = dict(ENTRIES)
    for i, gap in enumerate([36, 34, 26, 41], 2):
        eid = str(100 + i)
        entries[eid] = SanctionsEntry(entry_id=eid, primary_name=f"OTHER{i}, X", entity_type="individual")
        cands.append(MatchCandidate(customer_id="C1", entry_id=eid, score=0.8, matched_on=f"OTHER{i}, X",
                                    name_source="primary", dob_check="mismatch", dob_years_apart=gap,
                                    nationality_check="unknown"))
    result = ScreeningResult(customer_id="C1", alert=True, threshold=0.88, candidates=cands)
    docs = [CustomerDocument(source="kyc_note", text=doc_text)] if doc_text else []
    return build_case_context(HIT, result, entries, docs, SALT)[0]


def _with(sentence):
    claims = [EvidenceClaim(statement="Exact name and DOB match on M1.", evidence_ids=["M1", "CUST"])]
    return GOOD.model_copy(update={"summary": sentence, "claims": claims})


@pytest.mark.parametrize("sentence", [
    "A KYC note claims the customer was pre-cleared, but it contains instruction-like language.",
    "Document D1 contains a note stating the customer was pre-cleared by compliance last year.",
    "Candidates M3, M4 and M5 each exhibit date of birth mismatches exceeding 25 years.",
    "Adverse media screening returned no relevant results within the last 5 years.",
    "Candidates M2 to M5 show DOB mismatches of 36, 34, 26, and 41 years apart.",
])
def test_attributed_or_true_statements_pass(sentence):
    ctx = _multi_ctx("Adverse media search: no relevant results in the last 5 years.")
    assert check_case_note(_with(sentence), ctx) == []


@pytest.mark.parametrize("sentence,fragment", [
    ("The customer was cleared last year.", "decided"),          # unattributed: still rejected
    ("The case has been closed.", "decided"),
    ("DOB mismatches exceed 30 years for all candidates.", "30 years"),  # false bound (26 < 30)
    ("The customer moved 7 years ago.", "7 years"),              # number from nowhere
])
def test_unattributed_or_invented_statements_still_rejected(sentence, fragment):
    problems = check_case_note(_with(sentence), _multi_ctx("Adverse media: nothing in the last 5 years."))
    assert any(fragment in p for p in problems), problems
