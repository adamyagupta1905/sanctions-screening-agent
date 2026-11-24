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

## In progress
- [ ] Nothing yet; next step below.

## Next up (v1 checklist)
1. Data: script to download the public OFAC SDN list into `data/` (gitignored), parse to `SanctionsEntry`.
2. Synthetic customers generator: clean names, transliterations, aliases, DOB conflicts, true hits.
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
- 2026-11-23 Structured output via `messages.parse` + Pydantic. **Why:** one schema end to end. (ADR-002)
- 2026-11-23 Sanctions/KYC screening agent chosen. **Why:** covers agents, MCP, eval, guardrails, observability, model risk. (ADR-001)
- 2026-11-23 Libraries added only when code uses them. **Why:** keep requirements honest and pinned.

## Verification status
- Tests: 9 passing, `.venv/bin/python -m pytest -q`, 2026-11-23
- Evals: not built (answer key has 0 cases)
- Not verified: `ClaudeLLM` has never made a live API call (tested only with a fake client).
