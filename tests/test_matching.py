"""Matcher, baseline and DOB comparison tests. Fictional entries, no network."""

import random
from datetime import date

import pytest

from screening.dob import DobCheck, compare, parse_span
from screening.matching import ExactBaseline, Matcher, name_score
from screening.models import Customer, SanctionsEntry
from screening.normalize import normalize

ENTRIES = [
    SanctionsEntry(
        entry_id="1", primary_name="QUILLFEATHER, Marten Ivo", entity_type="individual",
        aliases=["HERONSBANE, Talon"], weak_aliases=["THE HERON"],
        dates_of_birth=["14 Mar 1968"], nationalities=["Freedonia"],
    ),
    SanctionsEntry(
        entry_id="2", primary_name="STORMWING, Ali Hassan Mohammed Ahmed", entity_type="individual",
        dates_of_birth=["circa 1970"],
    ),
    SanctionsEntry(entry_id="3", primary_name="QUILLFEATHER TRADING LLC", entity_type=None),
]


def cust(name, dob=None, nat=None, cid="C1"):
    return Customer(customer_id=cid, full_name=name, date_of_birth=dob, nationality=nat)


# --- DOB --------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,earliest,latest",
    [
        ("14 Mar 1968", date(1968, 3, 14), date(1968, 3, 14)),
        ("Mar 1968", date(1968, 3, 1), date(1968, 3, 31)),
        ("Feb 1968", date(1968, 2, 1), date(1968, 2, 29)),  # leap year
        ("1968", date(1968, 1, 1), date(1968, 12, 31)),
        ("circa 1968", date(1966, 1, 1), date(1970, 12, 31)),
        ("1966 to 1968", date(1966, 1, 1), date(1968, 12, 31)),
    ],
)
def test_parse_span_shapes(raw, earliest, latest):
    s = parse_span(raw)
    assert (s.earliest, s.latest) == (earliest, latest)


def test_parse_span_unknown_shape_is_none_not_guessed():
    assert parse_span("between 1960 and 1965 approx") is None
    assert parse_span("31 Feb 1968") is None


def test_compare_precision_levels():
    assert compare(date(1968, 3, 14), ["14 Mar 1968"]).check is DobCheck.EXACT
    assert compare(date(1968, 7, 1), ["1968"]).check is DobCheck.WITHIN_SPAN
    assert compare(date(1969, 7, 1), ["circa 1968"]).check is DobCheck.WITHIN_SPAN
    r = compare(date(1990, 1, 1), ["14 Mar 1968", "1970"])
    assert r.check is DobCheck.MISMATCH and r.closest_years_apart == 20
    assert compare(None, ["1968"]).check is DobCheck.UNKNOWN
    u = compare(date(1968, 1, 1), ["sometime in the 60s"])
    assert u.check is DobCheck.UNKNOWN and u.unparsed == ("sometime in the 60s",)


# --- name score -------------------------------------------------------------------------

def test_name_score_word_order_and_case_do_not_matter():
    assert name_score(normalize("Marten Ivo Quillfeather"), normalize("QUILLFEATHER, Marten Ivo")) == 1.0


def test_dropped_middle_name_still_high_but_below_full_match():
    s = name_score(normalize("Marten Quillfeather"), normalize("QUILLFEATHER, Marten Ivo"))
    assert 0.9 <= s < 1.0


def test_short_subset_of_long_name_is_penalised():
    s = name_score(normalize("Ali Hassan"), normalize("STORMWING, Ali Hassan Mohammed Ahmed"))
    assert s < 0.88  # token_set alone would give 1.0


def test_unrelated_names_score_low():
    assert name_score(normalize("Olivia Hartley"), normalize("QUILLFEATHER, Marten Ivo")) < 0.5


def test_prefilter_never_drops_a_name_that_scores_above_floor():
    """Regression: token_set_ratio is not always >= token_sort_ratio, so a single-scorer
    prefilter could silently drop real candidates."""
    rng = random.Random(0)
    words = ["ali", "hassan", "mohammed", "ivan", "petrov", "saad", "yunis", "quill"]
    entries = [
        SanctionsEntry(
            entry_id=str(i), entity_type="individual",
            primary_name=" ".join(rng.choice(words) for _ in range(rng.randint(2, 4))),
        )
        for i in range(150)
    ]
    m = Matcher(entries, threshold=0.88, floor=0.75)
    for k in range(40):
        name = " ".join(rng.choice(words) + ("x" if rng.random() < 0.3 else "") for _ in range(rng.randint(2, 4)))
        got = {c.entry_id for c in m.screen(cust(name, cid=f"C{k}")).candidates}
        want = {e.entry_id for e in entries if name_score(normalize(name), normalize(e.primary_name)) >= 0.75}
        assert got == want


# --- matcher ----------------------------------------------------------------------------

def test_typo_customer_alerts_with_evidence():
    r = Matcher(ENTRIES).screen(cust("Marten Ivo Quilfeather", date(1968, 3, 14), "Freedonia"))
    top = r.candidates[0]
    assert r.alert and top.entry_id == "1" and top.name_source == "primary"
    assert top.dob_check == "exact" and top.nationality_check == "match"


def test_alias_that_differs_from_primary_name_is_found():
    top = Matcher(ENTRIES).screen(cust("Talon Heronsbane")).candidates[0]
    assert top.entry_id == "1" and top.name_source == "alias" and top.score == 1.0


def test_weak_alias_is_discounted():
    top = Matcher(ENTRIES).screen(cust("The Heron")).candidates[0]
    assert top.name_source == "weak_alias" and top.score == pytest.approx(0.9)


def test_hard_negative_still_alerts_but_dob_mismatch_is_reported():
    r = Matcher(ENTRIES).screen(cust("Marten Ivo Quillfeather", date(1990, 5, 5), "Ruritania"))
    top = r.candidates[0]
    assert r.alert  # the alert is name-only on purpose
    assert top.dob_check == "mismatch" and top.dob_years_apart == 22
    assert top.nationality_check == "mismatch"


def test_entities_excluded_by_default():
    r = Matcher(ENTRIES).screen(cust("Quillfeather Trading LLC"))
    assert "3" not in {c.entry_id for c in r.candidates}


def test_clean_customer_no_alert_no_candidates():
    r = Matcher(ENTRIES).screen(cust("Olivia Hartley"))
    assert not r.alert and r.candidates == []


def test_candidates_between_floor_and_threshold_returned_without_alert():
    m = Matcher(ENTRIES, threshold=0.99, floor=0.75)
    r = m.screen(cust("Marten Quillfeather"))
    assert not r.alert and r.candidates and r.candidates[0].score < 0.99


def test_floor_above_threshold_rejected():
    with pytest.raises(ValueError):
        Matcher(ENTRIES, threshold=0.8, floor=0.9)


# --- baseline ---------------------------------------------------------------------------

def test_baseline_exact_only():
    b = ExactBaseline(ENTRIES)
    assert b.screen(cust("Ivo Marten QUILLFEATHER")).alert  # order/case ignored
    assert b.screen(cust("Talon Heronsbane")).candidates[0].name_source == "alias"
    assert not b.screen(cust("Marten Ivo Quilfeather")).alert  # one typo defeats it
    assert not b.screen(cust("The Heron")).alert  # weak aliases not used
