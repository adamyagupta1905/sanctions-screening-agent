"""Generate labelled synthetic customers from the downloaded OFAC list.

Run:  .venv/bin/python -m scripts.generate_customers [--n 500] [--seed 7]

Reads  data/ofac/entries.jsonl + data/ofac/manifest.json (run scripts.download_ofac first)
Writes data/synthetic/customers.jsonl     one LabelledCustomer per line
       data/synthetic/manifest.json       seed, n, OFAC snapshot sha256, counts

Same seed + same OFAC snapshot (sha256) = identical output, so an eval set can be
rebuilt exactly. Freezing a version into evaluation/ happens in the eval step.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from screening.models import SanctionsEntry
from screening.synthetic import Generator, summarize


def run(ofac_dir: Path, out_dir: Path, n: int, seed: int) -> dict:
    ofac_manifest = json.loads((ofac_dir / "manifest.json").read_text())
    with (ofac_dir / "entries.jsonl").open(encoding="utf-8") as fh:
        entries = [SanctionsEntry.model_validate_json(line) for line in fh]

    gen = Generator(entries, seed=seed)
    customers = gen.generate(n)

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "customers.jsonl").open("w", encoding="utf-8") as fh:
        for c in customers:
            fh.write(c.model_dump_json() + "\n")

    manifest = {
        "seed": seed,
        "n": n,
        "ofac_snapshot": {
            "retrieved_at": ofac_manifest["retrieved_at"],
            "sdn_sha256": ofac_manifest["files"]["SDN.CSV"]["sha256"],
            "alt_sha256": ofac_manifest["files"]["ALT.CSV"]["sha256"],
        },
        "rejected_clean_collisions": gen.rejected_clean_collisions,
        "summary": summarize(customers),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ofac-dir", type=Path, default=Path("data/ofac"))
    parser.add_argument("--out", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    manifest = run(args.ofac_dir, args.out, args.n, args.seed)
    print(json.dumps(manifest["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
