# Architecture decisions

Short records of choices that shape the system. Newest at the bottom.

## ADR-001: Project and stack
- **Date:** 2026-11-23
- **Decision:** Sanctions/KYC screening agent in Python 3.13 with Pydantic, pytest, Claude
  (`claude-opus-5-5`) behind a single adapter (`screening/llm.py`). Planned additions as they
  are needed: LangGraph (agent workflow with a human-approval interrupt), rapidfuzz (name
  matching), MCP server (expose screening tools), FastAPI (service), Streamlit (reviewer UI),
  SQLite (cases + audit log), OpenTelemetry/Langfuse-style tracing.
- **Alternatives:** Regulatory RAG copilot; SEC filings agent; LLM gateway platform.
- **Why:** Bank AI-engineer postings (JPMorgan, Goldman Sachs, Citi, Standard Chartered, HSBC,
  Scotiabank) repeatedly ask for agents with tools/MCP, evaluation with golden datasets,
  guardrails/PII/prompt-injection defence, observability and model-risk awareness. Screening
  covers all of these with a real public dataset (OFAC SDN) and a clear human-in-the-loop.
- **Revisit if:** OFAC data access or licensing changes, or the agent adds nothing over a
  plain pipeline in the eval (then say so and simplify).

## ADR-002: Structured output via `messages.parse` with a Pydantic schema
- **Date:** 2026-11-23
- **Decision:** `ClaudeLLM.extract` calls `client.messages.parse(output_format=Schema)`;
  `stop_reason == "refusal"` raises `LLMRefusal`, truncation raises `LLMIncomplete`.
- **Alternatives:** Tool-use JSON; raw `output_config.format` JSON schema.
- **Why:** SDK validates against the same Pydantic models the rest of the code uses.
- **Revisit if:** First live run: decide whether to enable server-side refusal fallbacks
  (beta). Not enabled yet because it has not been verified against a live call.

## ADR-003: OFAC ingestion from legacy CSV with a provenance manifest
- **Date:** 2026-11-23
- **Decision:** Ingest SDN.CSV + ALT.CSV (+ optional SDN_COMMENTS.CSV) with stdlib `csv` and
  `urllib`. Save the exact bytes per run, and write `manifest.json` with URL, sha256, size,
  encoding and row/orphan counts. Keep DOB/nationality as raw strings from Remarks.
- **Alternatives:** SDN_ADVANCED.XML (richer, structured DOBs, much more complex schema);
  third-party aggregators like OpenSanctions (easier, but adds a licence and a middleman).
- **Why:** CSV layout is short and documented (OFAC dat_spec.txt); the sha256 snapshot
  makes every screening result reproducible for audit; raw strings avoid inventing precision.
- **Revisit if:** DOB matching needs structured dates/ranges (move to SDN_ADVANCED.XML), or
  OFAC retires the legacy CSVs.
