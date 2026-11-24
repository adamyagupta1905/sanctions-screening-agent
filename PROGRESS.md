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
- [x] Matching module (`screening/matching.py`, `screening/dob.py`, ADR-005): fuzzy `Matcher`
      + `ExactBaseline`. Found and fixed a prefilter bug that could silently drop candidates
      (token_set is not always >= token_sort); regression test added.

- [x] Formal held-out eval (docs/EVALUATION.md): answer key v1, seed 2027, 1,000 customers
      incl. 300 new look-alike clean customers. Matcher vs exact baseline: true-match recall
      95.3% vs 45.0%, missed listed people 14 vs 165, look-alike false alerts 35/300 vs 0.

- [x] Safety layer before any LLM call (ADR-006): `screening/redaction.py`, `screening/untrusted.py`,
      `screening/context.py`. 24 committed injection/benign cases (`evaluation/injection_cases.json`).
      Detector flags 8/8 direct, 0/4 paraphrased, 1/4 obfuscated, 0/8 benign; containment
      (no escape from data blocks) 24/24.

- [x] LangGraph workflow (`screening/workflow.py`, `screening/case_note.py`, ADR-007): screen ->
      context -> LLM note (Python-checked, 1 retry, dropped if still failing) -> human interrupt
      -> record. Tested on every path with a scripted fake LLM. Fixed: an invalid analyst decision
      left the case permanently stuck (LangGraph replays the stored resume value); regression test.

- [x] Audit log + persistence (`screening/audit.py`, ADR-008): append-only, hash-chained SQLite
      log written by every node; `SqliteSaver` checkpointer, paused case resumes after restart.
      Tested: triggers block edits, direct edits and deletions detected by `verify()`, no raw
      customer identifiers in audit payloads, no-alert screenings logged too.

- [x] Case-note eval harness, offline (ADR-009): 38 frozen alerts, rule scoring, template
      baseline, record/replay. Template baseline: 38/38 DOB correct, 0 harmful, but only 7/14
      injection documents recognised (exactly the detector-flagged ones). Fixed a scoring bug
      (within-span DOB scored as unknown); regression test.

## Claude note run (on hold)
- [ ] Claude live run: not run; no Anthropic credentials. Estimate 38-76 calls, $2-5.
      2026-11-24: pilot approved but not run: no Anthropic credentials in the shell (`ant` CLI
      not installed, no ANTHROPIC_API_KEY); nothing billed. User has a Gemini key instead.
- [x] Gemini adapter added (ADR-010): `GeminiLLM`, `--provider gemini`, per-provider cassettes,
      `.env` loaded in live mode only. Tested offline with a fake client.
- [x] Live Gemini pilot (2026-11-24, user-approved): 3 cases (N01-N03, true_exact) on
      gemini-3.8-flash. 3 calls, all passed the Python checks first time, 3/3 DOB correct,
      0 harmful. Measured 2,873 input + 2,781 output tokens, about $0.013. Notes read by hand:
      cited, used only computed numbers, turned public list details into concrete checks.
      Cassette committed: `evaluation/cassettes/case_notes_v1_gemini.json`.
- [x] Fixed: LangGraph warned it was deserialising unregistered types from checkpoints (to
      be blocked in a future version). Allow-list in `screening/workflow.py`; passes with
      LANGGRAPH_STRICT_MSGPACK=true; regression test.

- [x] Full Gemini note run (2026-11-24, user-approved): 38 cases, 40 calls, ~$0.18.
      Re-scored: 0 harmful, 38/38 DOB correct, 14/14 injections recognised (template 7/14),
      1 hedged false flag on a benign doc. Reading the notes found 2 over-strict workflow
      checks (dropped 2 good notes) and 3 scorer bugs; all fixed with two-way regression tests.

## In progress
- [ ] Nothing. The scoring rules were fixed after reading Gemini's notes: a Claude run would be
      the out-of-sample test (needs Anthropic credentials + OK; estimate 38-76 calls, $2-5).
- [x] `ClaudeLLM` now logs token usage per call; runner reports totals. Fixed: a failed live
      run wrote an empty cassette that would have broken replay; regression test.

## Preliminary dev numbers (seed 7, n=500, threshold 0.88) - NOT the eval result
Tuning set only; the reported eval must use a different seed. Recall = source entry among
candidates at >= threshold. Script was a scratch check, formal version comes in the eval step.
| | Matcher | Exact baseline |
|---|---|---|
| Recall, all should_alert (300) | 0.963 | 0.653 |
| Recall, true matches (200) | 0.94 | 0.48 |
| Recall, typo (70) | 0.86 | 0.01 |
| Recall, dropped name (44) | 0.75 | 0.00 |
| Recall, transliteration (17) | 0.88 | 0.00 |
| Alerts on clean customers (200) | 0 | 0 |
- All 11 misses combine a dropped name with a typo or transliteration (2 words vs 3 lose
  too much). Per-token fuzzy matching is the likely fix; do not tune on this set.
- Hard negatives: all 100 alerted, and all 100 show `dob_check=mismatch`. DOB evidence is
  perfectly separating, but only because the generator built them that way.
- Speed: 500 customers in 16 s (about 32 ms each) against 7,573 individuals.


## Findings that shape the matching step
- DOBs are mixed precision: ~1,000 are year-only ("1965"), most are "dd Mon yyyy", some "circa".
  Matching must compare at the precision given, never pad a year to a full date.
- ~45% of entries have strong aliases; weak a.k.a.s (2,624 entries) should score lower.
- Entity types differ: customer screening should match individuals vs. individuals first.
- Strong aliases can be a completely different name (e.g. a nom de guerre): token similarity
  to the primary name will miss them, so the matcher must score every alias separately.
- Done: look-alike clean set added (35/300 false alerts at 0.88).
- Transliteration is rare in the generated set (17/200): if recall on that tag matters,
  generate a larger set or oversample entries whose names hit the variant table.

## Next up (v1 checklist)
1. ~~Data download + parse~~ done.
2. ~~Synthetic customers generator~~ done.
3. ~~Deterministic matching~~ done (dev numbers above).
4. ~~Answer key + eval~~ done for matching. Optional follow-up: per-token fuzzy matching for the
   dropped-name + typo misses (design on dev seed 7, re-measure on key v1).
5. ~~PII redaction + injection guard~~ done (LLM-side injection eval comes with step 8).
6. ~~LangGraph workflow~~ done (offline; no live model call yet).
7. ~~Audit log~~ done.
8. ~~Case-note eval~~ done on Gemini (docs/EVALUATION.md). Claude run optional.
9. MCP server exposing screening tools; FastAPI service; Streamlit reviewer UI.
10. Tracing/observability, model card (intended use, limits, eval results), README results table.

## Blockers / open questions
- Refusal fallbacks (beta) not enabled in `ClaudeLLM`; decide at first authorised live run.

## Decisions (newest first)
- 2026-11-24 Gemini added behind the same adapter; provider chosen per eval run. (ADR-010)
- 2026-11-24 Case notes scored by rules vs a template baseline; cassettes keyed by prompt hash. (ADR-009)
- 2026-11-24 Separate append-only hash-chained audit log; checkpoints are not the audit trail. (ADR-008)
- 2026-11-24 Fixed LangGraph flow with human interrupt; LLM note checked in Python. (ADR-007)
- 2026-11-24 Minimised case context; injection detection is a signal, never a gate. (ADR-006)
- 2026-11-24 Held-out key (seed 2027) frozen and committed; threshold stays 0.88 from dev. **Why:** no tuning on the reported set.
- 2026-11-23 Name-only alerts, blended fuzzy score, DOB/nationality as evidence. (ADR-005)
- 2026-11-23 Two labels per customer (should_alert, is_true_match); rule-built hard negatives. (ADR-004)
- 2026-11-23 OFAC via legacy CSV + provenance manifest; DOBs kept as raw text. (ADR-003)
- 2026-11-23 Structured output via `messages.parse` + Pydantic. **Why:** one schema end to end. (ADR-002)
- 2026-11-23 Sanctions/KYC screening agent chosen. **Why:** covers agents, MCP, eval, guardrails, observability, model risk. (ADR-001)
- 2026-11-23 Libraries added only when code uses them. **Why:** keep requirements honest and pinned.

## Verification status
- Tests: 169 passing, `.venv/bin/python -m pytest -q`, 2026-11-23
- Injection checks: deterministic, `evaluation/results/injection_checks_v1_2026-11-24.json`.
- Evals: matching eval run 2026-11-24 on key v1 (deterministic, no model); results in
  `evaluation/results/matching_key-v1_2026-11-24.json`. LLM evals: not built.
- Not verified: `ClaudeLLM` has never made a live API call (tested only with a fake client).
  The case-note prompt has never been run against Claude: quality, pass rate of the checks,
  and resistance to the injection cases are all unmeasured (step 8, needs your OK for cost).
- Not verified: audit chain head is not anchored externally; whole-chain rewrite is undetectable.
- Verified live: OFAC URLs and layout (2026-11-24). `--from-dir` run manually, no unit test.
- Not verified: weak-alias/DOB/nationality extraction checked by counts only, not hand-audited.
- Not verified: redactor on real-world document text (only synthetic strings); detector numbers on
  'direct' cases are optimistic because patterns and cases were written together.
- Not verified: clean customers are only checked for exact token-set collisions with list names;
  a clean name could still fuzzy-resemble a listed one (eval should surface these).
