# Progress

_Last updated: 2026-11-24 (handoff)_

## Current goal
v1 of a sanctions-screening system: Python scores customers against the OFAC SDN list, an LLM
drafts a cited case note behind a safety layer, a human decides, every step is audit-logged,
and each part is measured against a baseline. Core is built and measured; remaining work is
the service/UI layer, an optional second-model run, and portfolio polish.

## Done (details: docs/EVALUATION.md, docs/DECISIONS.md ADR-001..010, git log)
- [x] OFAC ingestion with sha256 provenance manifest (live: 19,629 entries, 0 orphans).
- [x] Labelled synthetic customers (true match / hard negative / clean / look-alike), seeded.
- [x] Deterministic matcher + exact baseline. Held-out eval (key v1, seed 2027, 1,000 customers):
      true-match recall 95.3% vs 45.0%, missed 14 vs 165, look-alike false alerts 35/300 vs 0.
- [x] Safety layer: minimised CaseContext, PII redaction, escaped `<data>` blocks, injection flags.
      Injection checks: containment 24/24; detector 8/8 direct, 0/4 paraphrased, 1/4 obfuscated.
- [x] LangGraph workflow: screen -> context -> LLM note (Python-checked) -> human interrupt -> record.
- [x] Append-only hash-chained SQLite audit log; SqliteSaver checkpointer (resumes after restart).
- [x] Providers: `ClaudeLLM` and `GeminiLLM` behind `StructuredLLM`; `--provider` in the note eval.
- [x] Case-note eval, live on gemini-3.8-flash (38 alerts, 40 calls, ~$0.18, user-approved):
      0 harmful notes, 38/38 DOB correct, 14/14 injected docs recognised (template 7/14).

- [x] FastAPI service + Streamlit reviewer page, key-free demo by replay (ADR-011). Clicked
      through N22 in the browser: injection warning shown, short reason rejected, escalation
      recorded, audit chain intact. Fixed while testing: UI listened on all interfaces (now
      localhost); audit could not separate two runs of one customer (now `run_id`).

## In progress
- Nothing.

## Next up (each startable on its own)
1. **MCP server** exposing read-only tools (`screen_customer`, `get_case`, `get_audit_history`);
   decisions stay in the UI, never an MCP tool (only a human decides).
2. **Model card** (`docs/MODEL_CARD.md`): intended use, data, both eval results, limits.
   Then run `/portfolio-release`.
3. Optional: **Claude note run** as the out-of-sample test of the scoring rules (they were
   corrected after reading Gemini's notes). Needs Anthropic credentials + the user's OK;
   estimate 38-76 calls, $2-5. Decide on refusal fallbacks (beta) at that point.
4. Optional: **per-token fuzzy matching** for the 13/14 misses that combine a dropped name with
   a typo/transliteration. Design on dev seed 7 only; re-measure on key v1.

## Blockers / open questions (waiting on the user)
- Claude run: no Anthropic credentials on this machine (`ant` CLI not installed). Gemini key is
  in the gitignored `.env` (moved there from `.env.example` on 2026-11-24; never committed).
- Nothing pushed; no remote configured. Ask before creating a GitHub repo or pushing.

## Decisions (newest first; full records in docs/DECISIONS.md)
- 2026-11-24 Note-eval rules fixed after reading Gemini's notes (5 bugs, two-way tests); a run
  on another model is the honest out-of-sample check. **Why:** rules misread correct notes.
- 2026-11-24 Gemini added behind the same adapter (ADR-010). **Why:** user has Gemini access.
- 2026-11-24 Case notes scored by rules vs a template baseline; prompt-keyed cassettes (ADR-009).
- 2026-11-24 Audit log separate from checkpoints, append-only, hash-chained (ADR-008).
- 2026-11-24 Fixed LangGraph flow; only the human node sets a Disposition (ADR-007).
- 2026-11-24 Injection detection is a signal, never a gate; model gets minimised context (ADR-006).
- 2026-11-24 Held-out key frozen; threshold 0.88 chosen on dev seed 7, never tuned on the key.
- 2026-11-23 Name-only alerts; DOB/nationality as evidence (ADR-005). Two labels per customer
  (ADR-004). OFAC legacy CSV + manifest (ADR-003). One Pydantic schema end to end (ADR-002).

## Verification status (2026-11-24)
- Tests: 180 passing, `.venv/bin/python -m pytest -q` (offline, ~2 s).
- Matching eval: reproduces 95.3% / 45.0% recall (`python -m evaluation.run_eval --no-write`).
- Injection checks: containment 24/24 (`python -m evaluation.injection_eval --no-write`).
- Note eval replay: 0 harmful, 14/14 injections, no API call
  (`python -m evaluation.run_note_eval --provider gemini --no-write`).
- Demo UI: verified by hand in the browser (N22, invalid then valid decision, audit chain).
- Not verified: Claude has never been called live; refusal fallbacks not enabled.
- Not verified: audit chain head not anchored externally (whole-chain rewrite undetectable).
- Not verified: redactor on real-world documents (synthetic strings only); detector "direct"
  numbers optimistic (patterns and cases written together).
- Not verified: OFAC Remarks extraction (DOB/nationality/weak aliases) checked by counts only.
- Limits of all evals: synthetic customers, one run, small n (see docs/EVALUATION.md).
