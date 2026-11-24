"""Build the frozen, held-out answer key for the screening eval.

Run once:  .venv/bin/python -m evaluation.build_answer_key
Writes:    evaluation/answer_key.json (committed)

Held-out rule: seed 7 is the development set used to pick the threshold. This key uses a
different seed and must never be used for tuning. If the key is rebuilt (e.g. new OFAC
snapshot), bump KEY_VERSION and say why in docs/EVALUATION.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from screening.models import SanctionsEntry
from screening.synthetic import Generator, summarize

KEY_VERSION = 1
HELD_OUT_SEED = 2027
N = 1000
SHARES = {"true_share": 0.30, "hard_share": 0.15, "lookalike_share": 0.30}  # rest: clean

ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = Path(__file__).with_name("answer_key.json")


def build(ofac_dir: Path = ROOT / "data" / "ofac") -> dict:
    manifest = json.loads((ofac_dir / "manifest.json").read_text())
    with (ofac_dir / "entries.jsonl").open(encoding="utf-8") as fh:
        entries = [SanctionsEntry.model_validate_json(line) for line in fh]
    gen = Generator(entries, seed=HELD_OUT_SEED)
    cases = gen.generate(N, **SHARES)
    return {
        "version": KEY_VERSION,
        "description": (
            "Held-out screening answer key. Each case: a synthetic customer, the kind, "
            "should_alert / is_true_match labels and the SDN entry it was derived from. "
            "Do not tune on this file; the dev set is seed 7."
        ),
        "seed": HELD_OUT_SEED,
        "n": N,
        "shares": SHARES,
        "ofac_snapshot": {
            "retrieved_at": manifest["retrieved_at"],
            "sdn_sha256": manifest["files"]["SDN.CSV"]["sha256"],
            "alt_sha256": manifest["files"]["ALT.CSV"]["sha256"],
        },
        "generator_stats": {
            "rejected_clean_collisions": gen.rejected_clean_collisions,
            "rejected_lookalikes": gen.rejected_lookalikes,
        },
        "summary": summarize(cases),
        "cases": [json.loads(c.model_dump_json()) for c in cases],
    }


def main() -> int:
    key = build()
    KEY_PATH.write_text(json.dumps(key, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(key["summary"], indent=2))
    print("rejected look-alikes (too close to a list name):", key["generator_stats"]["rejected_lookalikes"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
