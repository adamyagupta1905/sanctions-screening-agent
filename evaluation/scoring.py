"""Pure scoring for the screening eval: labelled cases + screening results -> metrics.

Business meaning of each metric:
- recall (should_alert): share of customers the bank must review that were alerted
  *on the right list entry*. Alerting on a different entry does not count.
- missed true matches: the harmful error. A sanctioned person onboarded without review.
  Listed one by one, never only as a rate.
- false alerts (clean / look-alike): analyst workload, not a compliance failure.
- candidates per alert: how many list entries the analyst must look at per alert.
- DOB evidence: for hard negatives, how often the evidence lets the reviewer clear
  them (dob mismatch shown); for true matches, how often the evidence would mislead
  (dob mismatch shown on the real person: should be zero).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from screening.models import ScreeningResult
from screening.synthetic import CustomerKind, LabelledCustomer


def _source_hit(result: ScreeningResult, case: LabelledCustomer, threshold: float):
    for c in result.candidates:
        if c.entry_id == case.source_entry_id and c.score >= threshold:
            return c
    return None


def _alerted(result: ScreeningResult, threshold: float) -> bool:
    return bool(result.candidates) and result.candidates[0].score >= threshold


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def score(
    cases: list[LabelledCustomer], results: dict[str, ScreeningResult], threshold: float
) -> dict:
    by_kind: dict[CustomerKind, list[LabelledCustomer]] = defaultdict(list)
    for case in cases:
        by_kind[case.kind].append(case)

    def recall(kinds: list[CustomerKind]) -> float | None:
        group = [c for k in kinds for c in by_kind[k]]
        return _rate(sum(_source_hit(results[c.customer.customer_id], c, threshold) is not None for c in group), len(group))

    missed = []
    for case in by_kind[CustomerKind.TRUE_MATCH]:
        res = results[case.customer.customer_id]
        if _source_hit(res, case, threshold) is None:
            source = next((c for c in res.candidates if c.entry_id == case.source_entry_id), None)
            missed.append({
                "customer_id": case.customer.customer_id,
                "customer_name": case.customer.full_name,
                "source_entry_id": case.source_entry_id,
                "source_score": source.score if source else None,
                "alerted_on_other_entry": _alerted(res, threshold),
                "perturbations": [p.value for p in case.perturbations],
            })

    tag_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in by_kind[CustomerKind.TRUE_MATCH]:
        hit = _source_hit(results[case.customer.customer_id], case, threshold) is not None
        for tag in {p.value for p in case.perturbations}:
            tag_counts[tag][0] += 1
            tag_counts[tag][1] += hit

    def false_alerts(kind: CustomerKind) -> dict:
        group = by_kind[kind]
        n_alert = sum(_alerted(results[c.customer.customer_id], threshold) for c in group)
        return {"n": len(group), "alerted": n_alert, "rate": _rate(n_alert, len(group))}

    alerted_results = [r for r in results.values() if _alerted(r, threshold)]
    cands_per_alert = _rate(
        sum(sum(c.score >= threshold for c in r.candidates) for r in alerted_results), len(alerted_results)
    )

    def dob_evidence(kind: CustomerKind) -> dict:
        counts: Counter[str] = Counter()
        for case in by_kind[kind]:
            hit = _source_hit(results[case.customer.customer_id], case, threshold)
            counts[hit.dob_check if hit else "not_alerted"] += 1
        return dict(sorted(counts.items()))

    return {
        "threshold": threshold,
        "n_cases": len(cases),
        "n_by_kind": {k.value: len(v) for k, v in sorted(by_kind.items(), key=lambda kv: kv[0].value)},
        "recall_should_alert": recall([CustomerKind.TRUE_MATCH, CustomerKind.HARD_NEGATIVE]),
        "recall_true_match": recall([CustomerKind.TRUE_MATCH]),
        "recall_hard_negative": recall([CustomerKind.HARD_NEGATIVE]),
        "missed_true_matches": len(missed),
        "missed_true_match_cases": missed,
        "recall_true_match_by_perturbation": {
            tag: {"n": n, "recall": _rate(h, n)} for tag, (n, h) in sorted(tag_counts.items())
        },
        "false_alerts_clean": false_alerts(CustomerKind.CLEAN),
        "false_alerts_lookalike": false_alerts(CustomerKind.LOOKALIKE_CLEAN),
        "alerts_per_100_customers": _rate(100 * len(alerted_results), len(results)),
        "candidates_per_alert": cands_per_alert,
        "dob_evidence_true_match": dob_evidence(CustomerKind.TRUE_MATCH),
        "dob_evidence_hard_negative": dob_evidence(CustomerKind.HARD_NEGATIVE),
    }
