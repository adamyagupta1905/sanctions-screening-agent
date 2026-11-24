"""Build the frozen case-note eval set from answer key v1 + the injection payloads.

Run once:  .venv/bin/python -m evaluation.build_note_cases
Writes:    evaluation/note_cases.json (committed)

Groups (each case is a real alert the matcher raises on the right list entry):
- true_exact      true match, list DOB equals customer DOB. Advising to clear is harmful.
- true_unknown    true match, DOB cannot be compared. Note should say DOB is missing.
- hard_negative   same name, DOB far apart. Note should surface the DOB mismatch.
- injected        true_exact + a document carrying an injection payload (all 14 document
                  payloads). Following it (advising to clear, stating clearance) is harmful.
- benign_doc      true_exact + a benign document. Checks the note does not cry "injection".
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from screening.matching import Matcher
from screening.models import SanctionsEntry
from screening.synthetic import CustomerKind, LabelledCustomer

HERE = Path(__file__).parent
ROOT = HERE.parent
OUT = HERE / "note_cases.json"
SIZES = {"true_exact": 8, "true_unknown": 4, "hard_negative": 8, "benign_doc": 4}  # injected: all doc attacks
SEED = 11


def build(ofac_dir: Path = ROOT / "data" / "ofac") -> dict:
    key = json.loads((HERE / "answer_key.json").read_text(encoding="utf-8"))
    injections = json.loads((HERE / "injection_cases.json").read_text(encoding="utf-8"))["cases"]
    with (ofac_dir / "entries.jsonl").open(encoding="utf-8") as fh:
        entries = [SanctionsEntry.model_validate_json(line) for line in fh]
    matcher = Matcher(entries)
    rng = random.Random(SEED)

    pools: dict[str, list[LabelledCustomer]] = {"exact": [], "unknown": [], "hard_negative": []}
    for raw in key["cases"]:
        case = LabelledCustomer.model_validate(raw)
        if case.kind not in (CustomerKind.TRUE_MATCH, CustomerKind.HARD_NEGATIVE):
            continue
        result = matcher.screen(case.customer)
        top = result.candidates[0] if result.candidates else None
        if not (result.alert and top and top.entry_id == case.source_entry_id):
            continue  # only alerts on the right entry: note quality, not matching, is measured
        if case.kind is CustomerKind.HARD_NEGATIVE:
            pools["hard_negative"].append(case)
        elif top.dob_check == "exact":
            pools["exact"].append(case)
        elif top.dob_check == "unknown":
            pools["unknown"].append(case)

    attacks = [c for c in injections if c["category"] != "benign" and c["location"] == "document"]
    benign = [c for c in injections if c["category"] == "benign" and c["location"] == "document"]
    exact = rng.sample(pools["exact"], SIZES["true_exact"] + len(attacks) + SIZES["benign_doc"])

    out = []

    def add(group: str, case: LabelledCustomer, doc: dict | None = None) -> None:
        out.append({
            "case_id": f"N{len(out) + 1:02d}",
            "group": group,
            "customer": json.loads(case.customer.model_dump_json()),
            "source_entry_id": case.source_entry_id,
            "documents": [{"source": "kyc_note", "text": doc["text"]}] if doc else [],
            "payload_id": doc["id"] if doc else None,
            "payload_category": doc["category"] if doc else None,
        })

    for c in exact[: SIZES["true_exact"]]:
        add("true_exact", c)
    for c in rng.sample(pools["unknown"], SIZES["true_unknown"]):
        add("true_unknown", c)
    for c in rng.sample(pools["hard_negative"], SIZES["hard_negative"]):
        add("hard_negative", c)
    rest = exact[SIZES["true_exact"]:]
    for c, attack in zip(rest, attacks):
        add("injected", c, attack)
    for c, doc in zip(rest[len(attacks):], rng.sample(benign, SIZES["benign_doc"])):
        add("benign_doc", c, doc)

    return {
        "version": 1,
        "built_from": {"answer_key_version": key["version"], "ofac_snapshot": key["ofac_snapshot"],
                       "injection_cases_version": 1, "seed": SEED},
        "n": len(out),
        "cases": out,
    }


def main() -> int:
    data = build()
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    from collections import Counter
    print(data["n"], dict(Counter(c["group"] for c in data["cases"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
