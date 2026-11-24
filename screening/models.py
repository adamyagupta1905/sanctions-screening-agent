"""Domain models shared across the pipeline.

Who fills each field matters for model risk, so it is written down here:
- Customer, SanctionsEntry: input data (synthetic customers, public OFAC list).
- MatchCandidate.score: computed by deterministic Python matching, never the LLM.
- CaseNote: written by the LLM; text only, every claim tied to an evidence id.
- Disposition: set by a human reviewer only.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class Customer(BaseModel):
    customer_id: str
    full_name: str
    date_of_birth: date | None = None  # missing stays missing
    nationality: str | None = None
    aliases: list[str] = Field(default_factory=list)


class SanctionsEntry(BaseModel):
    """One OFAC SDN record. Values are kept as published; nothing is guessed."""

    entry_id: str  # OFAC ent_num (internal id, not an official reference)
    primary_name: str
    entity_type: str | None = None  # "individual", "vessel", "aircraft"; None = entity
    programs: list[str] = Field(default_factory=list)
    title: str | None = None
    aliases: list[str] = Field(default_factory=list)  # strong aliases from ALT.CSV
    weak_aliases: list[str] = Field(default_factory=list)  # from Remarks; lower quality
    dates_of_birth: list[str] = Field(default_factory=list)  # raw text, e.g. "circa 1960"
    nationalities: list[str] = Field(default_factory=list)
    remarks: str | None = None


class MatchCandidate(BaseModel):
    """One possible hit. Every field is computed by deterministic Python (screening/matching.py)."""

    customer_id: str
    entry_id: str
    score: float = Field(ge=0.0, le=1.0)  # name similarity; DOB does not change it
    matched_on: str  # the list name (primary or alias) that produced the score
    name_source: str  # "primary" | "alias" | "weak_alias"
    dob_check: str  # "exact" | "within_span" | "mismatch" | "unknown"
    dob_years_apart: int | None = None  # set when dob_check == "mismatch"
    nationality_check: str  # "match" | "mismatch" | "unknown"
    unparsed_list_dobs: list[str] = Field(default_factory=list)  # shown, never guessed


class ScreeningResult(BaseModel):
    customer_id: str
    alert: bool  # any candidate at or above the alert threshold
    threshold: float
    candidates: list[MatchCandidate]  # best first


class EvidenceClaim(BaseModel):
    statement: str
    evidence_ids: list[str] = Field(min_length=1)  # no claim without a source


class CaseNote(BaseModel):
    """LLM output. It may recommend, but it cannot decide."""

    summary: str
    claims: list[EvidenceClaim]
    open_questions: list[str] = Field(default_factory=list)
    recommendation: str  # free text for the reviewer, not a disposition


class Disposition(str, Enum):
    PENDING = "pending"
    CLEARED = "cleared"  # false positive, closed by a human
    ESCALATED = "escalated"  # true/possible match, sent to compliance
