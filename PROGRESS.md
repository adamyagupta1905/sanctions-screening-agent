# Progress

_Last updated: 2026-11-24 (published)_

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

- [x] Read-only MCP server (ADR-012): 5 tools over the HTTP API, all `read_only_hint`, no decision
      tool; new audited `POST /lookup`. Tested through the MCP layer and over real stdio against the
      running demo (listed person found with exact DOB, clean name no alert, chain intact).

- [x] Model card (`docs/MODEL_CARD.md`) incl. false alerts by nationality (Russia 14/86 = 16%,
      Iran 1/36, Mexico 1/31, clean 0/250); `tests/test_model_card.py` ties every number to the
      results files (it caught a stale speed figure on first run).

- [x] Portfolio-release pass: README top screen (screenshot, diagram, stack, why, results with
      failures, limitations, docs index), `docs/ARCHITECTURE.md`. Committed the 24 Nov OFAC
      snapshot (`snapshots/ofac/2026-11-24/`, 1.2 MB gz, sha256-checked, ADR-013) so the demo and
      matching eval work on a fresh clone; CI now runs the matching eval instead of skipping it.

- [x] Published: public repo https://github.com/adamyagupta1905/sanctions-screening-agent
      (2026-11-24). CI green on GitHub (192 tests, snapshot load, matching eval reproduces
      95.3% / 45.0%, containment 24/24); README screenshot, tables and Mermaid diagrams render.
      CI actions bumped to checkout@v7 / setup-python@v7 (Node 24).

## In progress
- Nothing.

## Next up (each startable on its own)
1. Optional: swap the README screenshot for an alert that does not show a real listed name.
2. Optional: **Claude note run** as the out-of-sample test of the scoring rules (they were
   corrected after reading Gemini's notes). Needs Anthropic credentials + the user's OK;
   estimate 38-76 calls, $2-5. Decide on refusal fallbacks (beta) at that point.
3. Optional: **per-token fuzzy matching** for the 13/14 misses that combine a dropped name with
   a typo/transliteration. Design on dev seed 7 only; re-measure on key v1.

## Blockers / open questions (waiting on the user)
- Claude run: no Anthropic credentials on this machine (`ant` CLI not installed). Gemini key is
  in the gitignored `.env` (moved there from `.env.example` on 2026-11-24; never committed).
- Repo is public on GitHub (`origin`). Still ask before every push.

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
- Tests: 192 passing, `.venv/bin/python -m pytest -q` (offline, ~2 s).
- Matching eval: reproduces 95.3% / 45.0% recall (`python -m evaluation.run_eval --no-write`).
- Injection checks: containment 24/24 (`python -m evaluation.injection_eval --no-write`).
- Note eval replay: 0 harmful, 14/14 injections, no API call
  (`python -m evaluation.run_note_eval --provider gemini --no-write`).
- Demo UI: verified by hand in the browser (N22, invalid then valid decision, audit chain).
- Fresh copy without `data/`: `--snapshot` gives an identical manifest and entries.jsonl,
  run_eval reproduces 95.3% / 45.0%, demo API starts with 38 cases. Same checks green in GitHub CI.
- Not verified: Claude has never been called live; refusal fallbacks not enabled.
- Not verified: audit chain head not anchored externally (whole-chain rewrite undetectable).
- Not verified: redactor on real-world documents (synthetic strings only); detector "direct"
  numbers optimistic (patterns and cases written together).
- Not verified: OFAC Remarks extraction (DOB/nationality/weak aliases) checked by counts only.
- Limits of all evals: synthetic customers, one run, small n (see docs/EVALUATION.md).
