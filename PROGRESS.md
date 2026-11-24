# Progress

_Last updated: 2026-11-23_

## Current goal
Build v1: a screening pipeline that scores synthetic customers against the OFAC SDN list
in Python, has an LLM agent draft cited case notes, and records a human decision in an
append-only audit log, with an eval showing it beats an exact-match baseline.

## Done
- [x] Project chosen from bank AI-engineer job requirements (see docs/DECISIONS.md ADR-001)
- [x] Scaffold: CLAUDE.md, ADRs, CI, .env.example, pinned requirements
- [x] `screening/llm.py` adapter (`StructuredLLM` protocol, `ClaudeLLM`, refusal/truncation errors)
- [x] `screening/models.py` domain models (Customer, SanctionsEntry, MatchCandidate, CaseNote, Disposition)
- [x] Offline tests incl. "only llm.py imports the SDK" invariant test
- [x] OFAC parser (`screening/ofac.py`) + download script (`scripts/download_ofac.py`) with sha256 manifest; tested offline on fictional fixtures

- [x] First live OFAC download (2026-11-24 01:47 UTC): URLs confirmed working. 19,629 entries
      (10,144 entities, 7,573 individuals, 1,570 vessels, 342 aircraft), 20,257 aliases,
      36 comment spillovers, 0 orphans. 7,503/7,573 individuals have a DOB.
- [x] Fixed: live SDN_COMMENTS.CSV has 2 columns, not the 12 in OFAC's 2023 spec (regression test added).
- [x] Synthetic customer generator (`screening/synthetic.py`, `scripts/generate_customers.py`, ADR-004).
      Default run (n=500, seed=7): 200 true matches, 100 hard negatives, 200 clean; deterministic
      (byte-identical rerun). Perturbation counts: typo 70, drop_token 44, transliteration 17,
      strong_alias 15, list DOB year-only 19, list DOB missing 10, customer DOB missing 21.
- [x] Fixed: label leak via letter case (list-derived names kept OFAC UPPERCASE surnames);
      all names now share one style, regression test added.

## In progress
- [ ] Nothing; next step below.

## Findings that shape the matching step
- DOBs are mixed precision: ~1,000 are year-only ("1965"), most are "dd Mon yyyy", some "circa".
  Matching must compare at the precision given, never pad a year to a full date.
- ~45% of entries have strong aliases; weak a.k.a.s (2,624 entries) should score lower.
- Entity types differ: customer screening should match individuals vs. individuals first.
- Strong aliases can be a completely different name (e.g. a nom de guerre): token similarity
  to the primary name will miss them, so the matcher must score every alias separately.
- Transliteration is rare in the generated set (17/200): if recall on that tag matters,
  generate a larger set or oversample entries whose names hit the variant table.

## Next up (v1 checklist)
1. ~~Data download + parse~~ done.
2. ~~Synthetic customers generator~~ done.
3. Deterministic matching (`screening/matching.py`): normalisation + fuzzy scoring + DOB/nationality checks; exact-match baseline.
4. Answer key + eval (`/llm-eval`): precision/recall of matching vs. baseline, committed results.
5. PII redaction + prompt-injection guard for untrusted text; injection cases in the answer key.
6. LangGraph workflow: screen -> gather evidence -> draft CaseNote -> human interrupt -> record disposition.
7. Audit log (SQLite, append-only) with model, prompt version and input hashes.
8. Case-note eval: citation validity (rules) + faithfulness (LLM-judge, live, only when authorised).
9. MCP server exposing screening tools; FastAPI service; Streamlit reviewer UI.
10. Tracing/observability, model card (intended use, limits, eval results), README results table.

## Blockers / open questions
- Refusal fallbacks (beta) not enabled in `ClaudeLLM`; decide at first authorised live run.

## Decisions (newest first)
- 2026-11-23 Two labels per customer (should_alert, is_true_match); rule-built hard negatives. (ADR-004)
- 2026-11-23 OFAC via legacy CSV + provenance manifest; DOBs kept as raw text. (ADR-003)
- 2026-11-23 Structured output via `messages.parse` + Pydantic. **Why:** one schema end to end. (ADR-002)
- 2026-11-23 Sanctions/KYC screening agent chosen. **Why:** covers agents, MCP, eval, guardrails, observability, model risk. (ADR-001)
- 2026-11-23 Libraries added only when code uses them. **Why:** keep requirements honest and pinned.

## Verification status
- Tests: 38 passing, `.venv/bin/python -m pytest -q`, 2026-11-23
- Evals: not built (answer key has 0 cases)
- Not verified: `ClaudeLLM` has never made a live API call (tested only with a fake client).
- Verified live: OFAC URLs and layout (2026-11-24). `--from-dir` run manually, no unit test.
- Not verified: weak-alias/DOB/nationality extraction checked by counts only, not hand-audited.
- Not verified: clean customers are only checked for exact token-set collisions with list names;
  a clean name could still fuzzy-resemble a listed one (eval should surface these).
