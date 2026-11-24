import pytest
from pydantic import ValidationError

from screening.models import Customer, EvidenceClaim, MatchCandidate


def test_missing_optional_fields_stay_none():
    c = Customer(customer_id="c1", full_name="Jane Roe")
    assert c.date_of_birth is None and c.nationality is None


def test_score_must_be_between_0_and_1():
    with pytest.raises(ValidationError):
        MatchCandidate(customer_id="c1", entry_id="e1", score=1.2, matched_on="x")


def test_claim_without_evidence_is_rejected():
    with pytest.raises(ValidationError):
        EvidenceClaim(statement="unsupported", evidence_ids=[])
