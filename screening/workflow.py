"""The screening case workflow as a LangGraph state machine.

    screen ──no alert──> END (status "no_alert")
       │ alert
    build_context ──> draft_note ──(checks fail, 1 retry)──> draft_note
                          │ passes, or still failing (note dropped)
                      human_review  <- pauses here (interrupt) until an analyst decides
                          │
                       record ──> END (status "cleared" / "escalated")

Who does what:
- Python: screening, the case context, all checks on the LLM note, the state machine.
- LLM (behind StructuredLLM): drafts the CaseNote text only. Its schema has no field for
  a score or an outcome, so it cannot set one.
- Human: the only node that sets a Disposition. The graph cannot reach "record" without it.

Every node appends an event to `events` and, if an AuditLog is given, writes it to the
append-only audit log at the moment the node runs: what ran, when, for which case_ref,
against which list snapshot, with which prompt version, model and prompt hash, and who
decided what and why. Delivery is at-least-once: if the process dies after the audit
write but before LangGraph saves its checkpoint, the node re-runs and logs again.
"""

from __future__ import annotations

import hashlib
import operator
import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, ValidationError, field_validator

from screening.case_note import PROMPT_VERSION, SYSTEM_PROMPT, check_case_note, retry_prompt, user_prompt
from screening.audit import AuditLog
from screening.context import CaseContext, CustomerDocument, build_case_context, case_ref, render_for_prompt
from screening.llm import LLMIncomplete, LLMRefusal, StructuredLLM
from screening.matching import Matcher
from screening.models import CaseNote, Customer, Disposition, SanctionsEntry, ScreeningResult

MAX_NOTE_ATTEMPTS = 2


class ReviewDecision(BaseModel):
    """What the analyst submits to resume a paused case."""

    disposition: Disposition
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=10)  # a one-word reason is not an audit trail

    @field_validator("disposition")
    @classmethod
    def must_be_final(cls, v: Disposition) -> Disposition:
        if v is Disposition.PENDING:
            raise ValueError("a review must clear or escalate the case")
        return v


class CaseState(TypedDict, total=False):
    case_ref: str
    customer: Customer
    documents: list[CustomerDocument]
    result: ScreeningResult
    context: CaseContext
    rendered: str
    note: CaseNote | None
    note_problems: list[str]
    note_attempts: int
    decision: ReviewDecision
    status: str
    events: Annotated[list[dict[str, Any]], operator.add]


# Exactly the types a checkpoint may contain. Anything else is refused on load instead of
# being rebuilt from stored bytes (LangGraph warns on unregistered types and will block them).
CHECKPOINT_TYPES = [
    ("screening.models", "Customer"), ("screening.models", "ScreeningResult"),
    ("screening.models", "MatchCandidate"), ("screening.models", "CaseNote"),
    ("screening.models", "EvidenceClaim"), ("screening.models", "Disposition"),
    ("screening.context", "CaseContext"), ("screening.context", "CandidateFacts"),
    ("screening.context", "TextEvidence"), ("screening.context", "CustomerDocument"),
    ("screening.workflow", "ReviewDecision"),
]


def checkpoint_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


def sqlite_checkpointer(path: str) -> SqliteSaver:
    """Persistent store for paused cases (survives restarts). Holds full case state,
    including customer data: keep it in the same trust zone as the customer database."""
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False), serde=checkpoint_serde())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build_workflow(
    matcher: Matcher,
    entries_by_id: dict[str, SanctionsEntry],
    llm: StructuredLLM,
    salt: bytes,
    checkpointer: Any | None = None,
    audit: AuditLog | None = None,
    list_snapshot: str | None = None,
):
    """list_snapshot: e.g. the OFAC SDN.CSV sha256, recorded on every screening event."""
    model_name = getattr(llm, "model_name", type(llm).__name__)

    def emit(step: str, ref: str, **details: Any) -> dict[str, Any]:
        event = {"step": step, "case_ref": ref,
                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **details}
        if audit is not None:
            audit.append(event)
        return event

    def screen(state: CaseState) -> dict:
        ref = case_ref(state["customer"].customer_id, salt)
        result = matcher.screen(state["customer"])
        top = result.candidates[0].score if result.candidates else None
        return {
            "case_ref": ref,
            "result": result,
            "status": "alert" if result.alert else "no_alert",
            "events": [emit("screen", ref, alert=result.alert, top_score=top, n_candidates=len(result.candidates),
                            threshold=result.threshold, list_snapshot=list_snapshot)],
        }

    def build_context(state: CaseState) -> dict:
        # The redaction map is discarded here on purpose: nothing downstream needs it,
        # and the checkpointed state should hold as little PII as possible.
        context, _ = build_case_context(
            state["customer"], state["result"], entries_by_id, state.get("documents", []), salt
        )
        rendered = render_for_prompt(context)
        flags = {t.evidence_id: t.injection_flags for t in context.texts if t.injection_flags}
        return {
            "context": context,
            "rendered": rendered,
            "note_attempts": 0,
            "events": [emit("build_context", state["case_ref"],
                            redactions=context.redaction_counts, injection_flags=flags)],
        }

    def draft_note(state: CaseState) -> dict:
        attempt = state.get("note_attempts", 0) + 1
        problems = state.get("note_problems") or []
        prompt = retry_prompt(state["rendered"], problems) if attempt > 1 else user_prompt(state["rendered"])
        note: CaseNote | None
        try:
            note = llm.extract(system=SYSTEM_PROMPT, user=prompt, schema=CaseNote)
            new_problems = check_case_note(note, state["context"])
        except (LLMRefusal, LLMIncomplete) as exc:
            note, new_problems = None, [f"Model returned no usable note: {type(exc).__name__}: {exc}"]

        dropped = bool(new_problems) and attempt >= MAX_NOTE_ATTEMPTS
        return {
            "note": None if new_problems else note,
            "note_problems": new_problems,
            "note_attempts": attempt,
            "events": [emit("draft_note", state["case_ref"], attempt=attempt, model=model_name, prompt_version=PROMPT_VERSION,
                              prompt_sha256=_sha256(SYSTEM_PROMPT + "\n" + prompt),
                              passed_checks=not new_problems, problems=new_problems, note_dropped=dropped)],
        }

    def human_review(state: CaseState) -> dict:
        # Everything before interrupt() re-runs on resume, so this node has no side effects.
        # An invalid decision must not crash the node: LangGraph keeps the resume value, so
        # a crash would replay the same bad value forever and the case could never finish.
        # Instead we pause again, showing the error, until a valid decision arrives.
        context = state["context"]
        payload = {
            "case_ref": context.case_ref,
            "candidates": [c.model_dump() for c in context.candidates],
            "injection_flags": {t.evidence_id: t.injection_flags for t in context.texts if t.injection_flags},
            "note": state["note"].model_dump() if state.get("note") else None,
            "note_problems": state.get("note_problems", []),
            "decision_error": None,
        }
        while True:
            raw = interrupt(payload)
            try:
                decision = ReviewDecision.model_validate(raw)
                break
            except ValidationError as exc:
                payload = {**payload, "decision_error": str(exc)}
        return {"decision": decision,
                "events": [emit("human_review", state["case_ref"], reviewer=decision.reviewer,
                                  disposition=decision.disposition.value, reason=decision.reason)]}

    def record(state: CaseState) -> dict:
        d = state["decision"]
        return {"status": d.disposition.value,
                "events": [emit("record", state["case_ref"], disposition=d.disposition.value, reviewer=d.reviewer)]}

    def after_screen(state: CaseState) -> str:
        return "build_context" if state["result"].alert else END

    def after_note(state: CaseState) -> str:
        if state["note_problems"] and state["note_attempts"] < MAX_NOTE_ATTEMPTS:
            return "draft_note"
        return "human_review"

    graph = StateGraph(CaseState)
    for name, fn in [("screen", screen), ("build_context", build_context), ("draft_note", draft_note),
                     ("human_review", human_review), ("record", record)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "screen")
    graph.add_conditional_edges("screen", after_screen, ["build_context", END])
    graph.add_edge("build_context", "draft_note")
    graph.add_conditional_edges("draft_note", after_note, ["draft_note", "human_review"])
    graph.add_edge("human_review", "record")
    graph.add_edge("record", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver(serde=checkpoint_serde()))


def start_case(app, thread_id: str, customer: Customer, documents: list[CustomerDocument] | None = None) -> dict:
    """Run until the case finishes (no alert) or pauses for review. Returns the state."""
    return app.invoke({"customer": customer, "documents": documents or [], "events": []},
                      {"configurable": {"thread_id": thread_id}})


def submit_decision(app, thread_id: str, decision: dict) -> dict:
    """Resume a paused case with the analyst's decision. Validated first, so an invalid
    decision raises here and never reaches the graph."""
    ReviewDecision.model_validate(decision)
    return app.invoke(Command(resume=decision), {"configurable": {"thread_id": thread_id}})


def pending_review(app, thread_id: str) -> dict | None:
    """The payload shown to the analyst, or None if the case is not waiting for review."""
    snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
    for task in snapshot.tasks:
        for intr in task.interrupts:
            return intr.value
    return None
