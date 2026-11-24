"""Who bears the false alerts? Look-alike false-alert rate by customer nationality.

Run:  .venv/bin/python -m evaluation.false_alerts_by_group [--no-write]

Uses answer key v1 (look-alike clean customers: not on the list, names built from listed
people of the same nationality) and the matcher at its fixed threshold. Deterministic, free.
Groups with fewer than MIN_N customers are reported but marked too small to interpret.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from screening.matching import DEFAULT_THRESHOLD, Matcher
from screening.models import SanctionsEntry
from screening.synthetic import CustomerKind, LabelledCustomer

HERE = Path(__file__).parent
ROOT = HERE.parent
MIN_N = 15
OUT = HERE / "results" / "false_alerts_by_nationality_key-v1.json"


def run(ofac_dir: Path = ROOT / "data" / "ofac") -> dict | None:
    if not (ofac_dir / "entries.jsonl").exists():
        print("SKIPPED: OFAC data not found.")
        return None
    key = json.loads((HERE / "answer_key.json").read_text(encoding="utf-8"))
    with (ofac_dir / "entries.jsonl").open(encoding="utf-8") as fh:
        matcher = Matcher([SanctionsEntry.model_validate_json(line) for line in fh])
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    clean_alerts = clean_n = 0
    for raw in key["cases"]:
        case = LabelledCustomer.model_validate(raw)
        if case.kind is CustomerKind.LOOKALIKE_CLEAN:
            c = counts[case.customer.nationality or "unknown"]
            c[0] += 1
            c[1] += matcher.screen(case.customer).alert
        elif case.kind is CustomerKind.CLEAN:
            clean_n += 1
            clean_alerts += matcher.screen(case.customer).alert
    groups = sorted(
        ({"nationality": k, "n": n, "false_alerts": a, "rate": round(a / n, 4), "interpretable": n >= MIN_N}
         for k, (n, a) in counts.items()),
        key=lambda g: -g["n"],
    )
    return {
        "answer_key_version": key["version"],
        "threshold": DEFAULT_THRESHOLD,
        "min_n_to_interpret": MIN_N,
        "lookalike_total": {"n": sum(g["n"] for g in groups), "false_alerts": sum(g["false_alerts"] for g in groups)},
        "clean_total": {"n": clean_n, "false_alerts": clean_alerts},
        "by_nationality": groups,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    result = run()
    if result is None:
        return 0
    for g in result["by_nationality"]:
        flag = "" if g["interpretable"] else "  (too small)"
        print(f"{g['nationality']:26} {g['false_alerts']:3}/{g['n']:<3} {g['rate']:.0%}{flag}")
    if not args.no_write:
        OUT.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print("wrote", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
