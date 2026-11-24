"""Offline eval runner (skeleton).

Planned metrics once the pipeline exists:
- Matching: precision / recall / F1 vs. answer key, baseline = exact-name match.
- Case notes: every claim cites a real evidence id; no invented facts (LLM-judge + rules).
- Safety: prompt-injection cases must not change scores or dispositions.

Run: .venv/bin/python -m evaluation.run_eval
"""

from __future__ import annotations

import json
from pathlib import Path

KEY = Path(__file__).with_name("answer_key.json")


def main() -> int:
    key = json.loads(KEY.read_text())
    n = len(key["cases"])
    if n == 0:
        print("Answer key has 0 cases: eval not built yet (see PROGRESS.md).")
        return 0
    raise NotImplementedError("Scoring not built yet.")


if __name__ == "__main__":
    raise SystemExit(main())
