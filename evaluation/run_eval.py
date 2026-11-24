"""Screening eval: Matcher vs ExactBaseline on the frozen held-out answer key.

Run:  .venv/bin/python -m evaluation.run_eval            # score and write results
      .venv/bin/python -m evaluation.run_eval --no-write # print only

Fully offline and free: the matcher is deterministic Python, no model calls.
Needs data/ofac (run scripts.download_ofac). If the data is missing (e.g. in CI) the eval
is skipped with a message. If the OFAC snapshot differs from the one the key was built
on, the eval refuses to run unless --allow-snapshot-mismatch is given, because the
labels (which entry each customer came from) are tied to that snapshot.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from evaluation.scoring import score
from screening.matching import DEFAULT_THRESHOLD, ExactBaseline, Matcher
from screening.models import SanctionsEntry
from screening.synthetic import LabelledCustomer

ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = Path(__file__).with_name("answer_key.json")
RESULTS_DIR = Path(__file__).with_name("results")
SWEEP = [0.80, 0.84, 0.86, 0.88, 0.90, 0.92, 0.95]  # reported for information only


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
        return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "") if out.returncode == 0 else None
    except OSError:
        return None


def run(ofac_dir: Path, allow_mismatch: bool = False) -> dict | None:
    key = json.loads(KEY_PATH.read_text(encoding="utf-8"))
    if not key.get("cases"):
        print("Answer key has 0 cases: run `python -m evaluation.build_answer_key`.")
        return None
    if not (ofac_dir / "entries.jsonl").exists():
        print(f"SKIPPED: {ofac_dir}/entries.jsonl not found. Run `python -m scripts.download_ofac`.")
        return None

    manifest = json.loads((ofac_dir / "manifest.json").read_text())
    have = manifest["files"]["SDN.CSV"]["sha256"]
    want = key["ofac_snapshot"]["sdn_sha256"]
    if have != want and not allow_mismatch:
        raise SystemExit(
            f"OFAC snapshot differs from the answer key's (have {have[:12]}, key built on {want[:12]}). "
            "Rebuild the key or pass --allow-snapshot-mismatch (results not comparable)."
        )

    with (ofac_dir / "entries.jsonl").open(encoding="utf-8") as fh:
        entries = [SanctionsEntry.model_validate_json(line) for line in fh]
    cases = [LabelledCustomer.model_validate(c) for c in key["cases"]]

    t0 = time.perf_counter()
    # Screen once with the lowest threshold used anywhere; scoring applies each threshold.
    matcher = Matcher(entries, threshold=DEFAULT_THRESHOLD, floor=min(SWEEP))
    m_results = {c.customer.customer_id: matcher.screen(c.customer) for c in cases}
    matcher_secs = time.perf_counter() - t0
    baseline = ExactBaseline(entries)
    b_results = {c.customer.customer_id: baseline.screen(c.customer) for c in cases}

    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "answer_key": {"version": key["version"], "seed": key["seed"], "n": key["n"]},
        "ofac_snapshot": key["ofac_snapshot"],
        "snapshot_matches_key": have == want,
        "models_used": "none (deterministic Python matcher)",
        "matcher": {
            "threshold": DEFAULT_THRESHOLD,
            "threshold_source": "chosen on dev seed 7, not tuned on this key",
            "seconds_total": round(matcher_secs, 2),
            "ms_per_customer": round(1000 * matcher_secs / len(cases), 1),
            "metrics": score(cases, m_results, DEFAULT_THRESHOLD),
        },
        "baseline": {"name": "ExactBaseline", "metrics": score(cases, b_results, 1.0)},
        "matcher_threshold_sweep_info_only": [
            {k: v for k, v in score(cases, m_results, t).items() if not k.endswith("_cases")
             and k not in ("recall_true_match_by_perturbation", "n_by_kind", "n_cases")}
            for t in SWEEP
        ],
    }


def table(result: dict) -> str:
    m, b = result["matcher"]["metrics"], result["baseline"]["metrics"]

    def pct(x):
        return "n/a" if x is None else f"{100 * x:.1f}%"

    rows = [
        ("Recall, customers that must be reviewed", pct(m["recall_should_alert"]), pct(b["recall_should_alert"])),
        ("Recall, true matches", pct(m["recall_true_match"]), pct(b["recall_true_match"])),
        ("Missed true matches (harmful)", str(m["missed_true_matches"]), str(b["missed_true_matches"])),
        ("False alerts, clean", f'{m["false_alerts_clean"]["alerted"]}/{m["false_alerts_clean"]["n"]}',
         f'{b["false_alerts_clean"]["alerted"]}/{b["false_alerts_clean"]["n"]}'),
        ("False alerts, look-alike clean", f'{m["false_alerts_lookalike"]["alerted"]}/{m["false_alerts_lookalike"]["n"]}',
         f'{b["false_alerts_lookalike"]["alerted"]}/{b["false_alerts_lookalike"]["n"]}'),
        ("Candidates per alert", str(m["candidates_per_alert"]), str(b["candidates_per_alert"])),
    ]
    lines = ["| Metric | Matcher | Exact baseline |", "|---|---|---|"]
    lines += [f"| {a} | {x} | {y} |" for a, x, y in rows]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ofac-dir", type=Path, default=ROOT / "data" / "ofac")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--allow-snapshot-mismatch", action="store_true")
    args = parser.parse_args(argv)

    result = run(args.ofac_dir, args.allow_snapshot_mismatch)
    if result is None:
        return 0
    print(table(result))
    if not args.no_write:
        RESULTS_DIR.mkdir(exist_ok=True)
        day = result["run_at"][:10]
        out = RESULTS_DIR / f"matching_key-v{result['answer_key']['version']}_{day}.json"
        out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
