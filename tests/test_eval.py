"""Eval scoring tests on hand-built cases, so the eval code cannot silently break."""

import json

import pytest

from evaluation import run_eval
from evaluation.scoring import score
from screening.models import Customer, MatchCandidate, ScreeningResult
from screening.synthetic import CustomerKind, LabelledCustomer, Perturbation


def case(cid, kind, source=None, tags=()):
    alert = kind in (CustomerKind.TRUE_MATCH, CustomerKind.HARD_NEGATIVE)
    return LabelledCustomer(
        customer=Customer(customer_id=cid, full_name=f"Name {cid}"),
        kind=kind, should_alert=alert, is_true_match=kind is CustomerKind.TRUE_MATCH,
        source_entry_id=source, perturbations=list(tags),
    )


def result(cid, *cands):
    cs = [
        MatchCandidate(customer_id=cid, entry_id=e, score=s, matched_on="x", name_source="primary",
                       dob_check=dob, nationality_check="unknown")
        for e, s, dob in cands
    ]
    cs.sort(key=lambda c: -c.score)
    return ScreeningResult(customer_id=cid, alert=bool(cs) and cs[0].score >= 0.88, threshold=0.88, candidates=cs)


CASES = [
    case("T1", CustomerKind.TRUE_MATCH, "100", [Perturbation.TYPO]),
    case("T2", CustomerKind.TRUE_MATCH, "200", [Perturbation.TYPO, Perturbation.DROP_TOKEN]),
    case("T3", CustomerKind.TRUE_MATCH, "300", [Perturbation.REORDER]),
    case("H1", CustomerKind.HARD_NEGATIVE, "400"),
    case("C1", CustomerKind.CLEAN),
    case("L1", CustomerKind.LOOKALIKE_CLEAN),
    case("L2", CustomerKind.LOOKALIKE_CLEAN),
]
RESULTS = {
    "T1": result("T1", ("100", 0.95, "exact")),                       # hit
    "T2": result("T2", ("200", 0.80, "exact")),                       # source below threshold: miss
    "T3": result("T3", ("999", 0.97, "unknown"), ("300", 0.85, "exact")),  # alerted on WRONG entry: miss
    "H1": result("H1", ("400", 1.0, "mismatch")),
    "C1": result("C1"),
    "L1": result("L1", ("555", 0.90, "unknown"), ("556", 0.89, "unknown")),  # false alert, 2 candidates
    "L2": result("L2", ("557", 0.70, "unknown")),
}


def test_recall_counts_only_the_right_entry():
    m = score(CASES, RESULTS, 0.88)
    assert m["recall_true_match"] == pytest.approx(1 / 3, abs=1e-4)  # rates rounded to 4 dp
    assert m["recall_hard_negative"] == 1.0
    assert m["recall_should_alert"] == 0.5


def test_missed_true_matches_are_listed_individually():
    m = score(CASES, RESULTS, 0.88)
    missed = {x["customer_id"]: x for x in m["missed_true_match_cases"]}
    assert m["missed_true_matches"] == 2 and set(missed) == {"T2", "T3"}
    assert missed["T2"]["source_score"] == 0.80 and not missed["T2"]["alerted_on_other_entry"]
    assert missed["T3"]["alerted_on_other_entry"]  # alert fired, but on the wrong person


def test_false_alerts_workload_and_dob_evidence():
    m = score(CASES, RESULTS, 0.88)
    assert m["false_alerts_clean"] == {"n": 1, "alerted": 0, "rate": 0.0}
    assert m["false_alerts_lookalike"] == {"n": 2, "alerted": 1, "rate": 0.5}
    # alerted: T1(1 cand >= .88), T3(1), H1(1), L1(2) -> 5 candidates / 4 alerts
    assert m["candidates_per_alert"] == 1.25
    assert m["alerts_per_100_customers"] == pytest.approx(100 * 4 / 7, abs=1e-3)
    assert m["dob_evidence_hard_negative"] == {"mismatch": 1}
    assert m["dob_evidence_true_match"] == {"exact": 1, "not_alerted": 2}


def test_per_perturbation_recall():
    tags = score(CASES, RESULTS, 0.88)["recall_true_match_by_perturbation"]
    assert tags["typo"] == {"n": 2, "recall": 0.5}
    assert tags["drop_token"] == {"n": 1, "recall": 0.0}


def test_lower_threshold_changes_outcome():
    m = score(CASES, RESULTS, 0.80)
    assert m["recall_true_match"] == 1.0  # T2 and T3's source now reach the threshold


def test_run_skips_cleanly_without_ofac_data(tmp_path, capsys):
    assert run_eval.run(tmp_path) is None
    assert "SKIPPED" in capsys.readouterr().out


def test_run_refuses_mismatched_snapshot(tmp_path):
    (tmp_path / "entries.jsonl").write_text("")
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {"SDN.CSV": {"sha256": "not-the-key-snapshot"}}}))
    with pytest.raises(SystemExit, match="snapshot differs"):
        run_eval.run(tmp_path)


def test_committed_answer_key_is_well_formed():
    key = json.loads(run_eval.KEY_PATH.read_text(encoding="utf-8"))
    cases = [LabelledCustomer.model_validate(c) for c in key["cases"]]
    assert len(cases) == key["n"] and key["seed"] != 7  # held out from the dev seed
    assert len({c.customer.customer_id for c in cases}) == len(cases)
    for c in cases:
        assert c.should_alert == (c.kind in (CustomerKind.TRUE_MATCH, CustomerKind.HARD_NEGATIVE))
        assert (c.source_entry_id is not None) == c.should_alert
