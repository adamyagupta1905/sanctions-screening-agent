"""Case-note eval: Claude (through the real workflow) vs a no-LLM template note.

  .venv/bin/python -m evaluation.run_note_eval                 # replay cassette (free)
  .venv/bin/python -m evaluation.run_note_eval --live          # CALLS CLAUDE, COSTS MONEY
  .venv/bin/python -m evaluation.run_note_eval --live --max-cases 3   # small paid pilot

Live mode needs ANTHROPIC_API_KEY (or `ant auth login`) and the user's explicit OK.
It records every response to evaluation/cassettes/case_notes_v1.json so later runs replay
for free. Without a cassette, only the template baseline is scored.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from evaluation.cassette import RecordingLLM, ReplayLLM
from evaluation.note_scoring import aggregate, score_note, template_note
from screening.case_note import PROMPT_VERSION
from screening.context import CustomerDocument, build_case_context
from screening.matching import Matcher
from screening.models import CaseNote, Customer, SanctionsEntry
from screening.workflow import build_workflow, pending_review, start_case

HERE = Path(__file__).parent
ROOT = HERE.parent
CASES = HERE / "note_cases.json"
CASSETTE = HERE / "cassettes" / "case_notes_v1.json"
EVAL_SALT = b"note-eval-salt-not-a-secret"  # fixed so prompts (and cassette keys) are stable


def _load(ofac_dir: Path):
    data = json.loads(CASES.read_text(encoding="utf-8"))
    if not (ofac_dir / "entries.jsonl").exists():
        return data, None
    manifest = json.loads((ofac_dir / "manifest.json").read_text())
    if manifest["files"]["SDN.CSV"]["sha256"] != data["built_from"]["ofac_snapshot"]["sdn_sha256"]:
        raise SystemExit("OFAC snapshot differs from the one note_cases.json was built on.")
    with (ofac_dir / "entries.jsonl").open(encoding="utf-8") as fh:
        entries = [SanctionsEntry.model_validate_json(line) for line in fh]
    return data, entries


def _usage(llm) -> dict | None:
    calls = getattr(getattr(llm, "inner", None), "usage", None)
    if not calls:
        return None
    return {"calls": len(calls), "input_tokens": sum(c["input_tokens"] for c in calls),
            "output_tokens": sum(c["output_tokens"] for c in calls)}


def run(ofac_dir: Path, live: bool = False, max_cases: int | None = None) -> dict | None:
    data, entries = _load(ofac_dir)
    if entries is None:
        print(f"SKIPPED: {ofac_dir}/entries.jsonl not found.")
        return None
    entries_by_id = {e.entry_id: e for e in entries}
    matcher = Matcher(entries)
    cases = data["cases"][:max_cases] if max_cases else data["cases"]

    baseline_rows = []
    for case in cases:
        customer = Customer.model_validate(case["customer"])
        docs = [CustomerDocument(**d) for d in case["documents"]]
        ctx, _ = build_case_context(customer, matcher.screen(customer), entries_by_id, docs, EVAL_SALT)
        baseline_rows.append({"case_id": case["case_id"], "group": case["group"],
                              "verdict": score_note(template_note(ctx), ctx, case["group"])})

    if live:
        from screening.llm import ClaudeLLM  # only imported when actually spending money
        llm = RecordingLLM(ClaudeLLM())
    elif CASSETTE.exists():
        llm = ReplayLLM(CASSETTE)
    else:
        llm = None

    llm_rows, attempts = [], []
    if llm is not None:
        app = build_workflow(matcher, entries_by_id, llm, EVAL_SALT)
        try:
            for case in cases:
                customer = Customer.model_validate(case["customer"])
                docs = [CustomerDocument(**d) for d in case["documents"]]
                state = start_case(app, case["case_id"], customer, docs)
                payload = pending_review(app, case["case_id"])
                note = CaseNote.model_validate(payload["note"]) if payload["note"] else None
                drafts = [e for e in state["events"] if e["step"] == "draft_note"]
                attempts.append({"case_id": case["case_id"], "attempts": len(drafts),
                                 "first_attempt_passed": drafts[0]["passed_checks"],
                                 "problems": [e["problems"] for e in drafts]})
                llm_rows.append({"case_id": case["case_id"], "group": case["group"],
                                 "payload_id": case["payload_id"], "payload_category": case["payload_category"],
                                 "note": payload["note"], "verdict": score_note(note, state["context"], case["group"])})
        finally:
            if live:
                llm.save(CASSETTE)

    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if live else ("replay" if llm else "baseline_only"),
        "model": getattr(llm, "model_name", None),
        "prompt_version": PROMPT_VERSION,
        "note_cases_version": data["version"],
        "n_cases": len(cases),
        "model_calls": getattr(llm, "calls", 0),
        "token_usage": _usage(llm),
        "baseline": {"name": "template_note (no LLM)", "summary": aggregate(baseline_rows), "cases": baseline_rows},
        "llm": None if llm is None else {
            "summary": aggregate(llm_rows),
            "first_attempt_pass": sum(a["first_attempt_passed"] for a in attempts),
            "notes_dropped": sum(r["note"] is None for r in llm_rows),
            "attempts": attempts,
            "cases": llm_rows,
        },
    }


def table(result: dict) -> str:
    b = result["baseline"]["summary"]["by_group"]
    m = result["llm"]["summary"]["by_group"] if result["llm"] else None
    lines = ["| Group | n | Metric | Template | Claude |", "|---|---|---|---|---|"]
    for g in b:
        for metric in ("dob_correct", "advises_clearing", "mentions_injection"):
            bv = b[g][metric]
            mv = m[g][metric] if m else None
            lines.append(f"| {g} | {b[g]['n']} | {metric} | {bv['true']}/{bv['n']} | "
                         f"{f'{mv['true']}/{mv['n']}' if mv else 'not run'} |")
        lines.append(f"| {g} | {b[g]['n']} | harmful notes | {b[g]['harmful_notes']} | "
                     f"{m[g]['harmful_notes'] if m else 'not run'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="call Claude (costs money)")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--ofac-dir", type=Path, default=ROOT / "data" / "ofac")
    args = parser.parse_args(argv)
    result = run(args.ofac_dir, live=args.live, max_cases=args.max_cases)
    if result is None:
        return 0
    print(f"mode={result['mode']} model={result['model']} calls={result['model_calls']} usage={result['token_usage']}")
    print(table(result))
    if not args.no_write and result["mode"] != "baseline_only":
        out = HERE / "results" / f"case_notes_{result['mode']}_{result['run_at'][:10]}.json"
        out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
