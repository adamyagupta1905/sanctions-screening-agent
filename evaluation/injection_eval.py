"""Deterministic checks of the untrusted-text defences (no model calls, free).

Run:  .venv/bin/python -m evaluation.injection_eval [--no-write]

For every case in injection_cases.json:
1. Detector: does scan_for_injection flag it? Reported per category, including the
   misses on paraphrased/obfuscated payloads (expected: the detector is a signal, not
   the defence) and false flags on benign text.
2. Containment: the case is placed in a real CaseContext (customer name or document)
   and rendered. The rendered prompt must contain exactly one closing </data> per block
   and no raw '<system>' tag, i.e. the payload cannot break out of its data block.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from screening.context import CustomerDocument, build_case_context, render_for_prompt
from screening.models import Customer, MatchCandidate, SanctionsEntry, ScreeningResult
from screening.untrusted import scan_for_injection

HERE = Path(__file__).parent
CASES_PATH = HERE / "injection_cases.json"
SALT = b"eval-only-salt-not-a-secret"

_ENTRY = SanctionsEntry(entry_id="1", primary_name="QUILLFEATHER, Marten", entity_type="individual",
                        dates_of_birth=["14 Mar 1968"])


def _render(case: dict) -> tuple[str, int]:
    name = case["text"] if case["location"] == "customer_name" else "Marten Quillfeather"
    docs = [CustomerDocument(source="kyc_note", text=case["text"])] if case["location"] == "document" else []
    customer = Customer(customer_id="C1", full_name=name)
    result = ScreeningResult(customer_id="C1", alert=True, threshold=0.88, candidates=[
        MatchCandidate(customer_id="C1", entry_id="1", score=0.95, matched_on="QUILLFEATHER, Marten",
                       name_source="primary", dob_check="unknown", nationality_check="unknown")])
    ctx, _ = build_case_context(customer, result, {"1": _ENTRY}, docs, SALT)
    return render_for_prompt(ctx), len(ctx.texts)


def run() -> dict:
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    per_cat: dict[str, dict] = defaultdict(lambda: {"n": 0, "flagged": 0, "missed": [], "false_flags": []})
    contained = []
    for case in data["cases"]:
        flags = scan_for_injection(case["text"])
        cat = per_cat[case["category"]]
        cat["n"] += 1
        cat["flagged"] += bool(flags)
        if case["expect_flag"] and not flags:
            cat["missed"].append(case["id"])
        if not case["expect_flag"] and flags:
            cat["false_flags"].append({"id": case["id"], "patterns": flags})

        rendered, n_blocks = _render(case)
        contained.append(
            rendered.count("</data>") == n_blocks
            and "<system>" not in rendered.lower()
            and "</system>" not in rendered.lower()
        )

    attacks = [c for k, c in per_cat.items() if k != "benign"]
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases_version": data["version"],
        "models_used": "none (deterministic checks)",
        "detector_by_category": dict(per_cat),
        "detector_attack_recall": round(sum(c["flagged"] for c in attacks) / sum(c["n"] for c in attacks), 4),
        "detector_benign_false_flags": per_cat["benign"]["flagged"],
        "containment_ok": f"{sum(contained)}/{len(contained)}",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    result = run()
    for cat, c in result["detector_by_category"].items():
        extra = f" missed={c['missed']}" if c["missed"] else ""
        extra += f" false_flags={c['false_flags']}" if c["false_flags"] else ""
        print(f"{cat:11} flagged {c['flagged']}/{c['n']}{extra}")
    print("containment:", result["containment_ok"])
    if not args.no_write:
        out = HERE / "results" / f"injection_checks_v{result['cases_version']}_{result['run_at'][:10]}.json"
        out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print("wrote", out.relative_to(HERE.parent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
