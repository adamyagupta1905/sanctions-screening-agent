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

## ADR-004: Synthetic customers with two labels and constructed hard negatives
- **Date:** 2026-11-23
- **Decision:** Each customer carries `should_alert` (name close enough to review) and
  `is_true_match` (actually the listed person). Mix: 40% true matches (real SDN individuals,
  perturbed: reorder, punctuation, typo, dropped middle name, known transliterations, strong
  alias), 20% hard negatives (same name, DOB >= 10 years from every list DOB, different
  nationality), 40% clean (fixed name pool, collisions with list names rejected). Seeded.
- **Alternatives:** Only alert/no-alert labels; LLM-generated customers; real KYC data.
- **Why:** Banks measure two things: alert recall (missing a hit is a regulatory failure) and
  false-positive workload. Two labels let the eval measure both, and the hard negatives are
  exactly the cases the case note + reviewer must resolve. Rule-based generation means the
  labels are true by construction; an LLM generator could not guarantee that.
- **Limits:** Perturbations are simpler than real data (no script changes, e.g. Cyrillic/Arabic
  originals). Clean names are only checked for exact token-set collisions, so a clean
  customer could still fuzzy-resemble a listed name; the eval should report such cases.
- **Revisit if:** Matching scores near 100% (data too easy: add harder perturbations).

## ADR-005: Name-only alerts from a blended fuzzy score; DOB as evidence
- **Date:** 2026-11-23
- **Decision:** Score every list name (primary, strong alias, weak alias x0.9) with
  max(token_sort, token_set - 0.06 per word-count difference) using rapidfuzz. Alert when
  the best score >= 0.88. DOB and nationality checks are attached to each candidate but do
  not change the score or the alert. Prefilter with both scorers (union).
- **Alternatives:** Jaro-Winkler per token; phonetic keys (Double Metaphone); combining DOB
  into one score; an embedding model; an LLM matcher.
- **Why:** Explainable to a reviewer and a model-risk team (two named, standard measures plus
  one penalty), fast enough (about 30 ms per customer against 7.5k individuals), and keeping
  DOB out of the alert avoids hiding hits where the list DOB is missing or vague. An LLM
  matcher would be slower, costlier and hard to validate for a regulated control.
- **Revisit if:** The held-out eval shows the dev misses (dropped name + typo) matter, then
  try per-token fuzzy matching; or the harder clean set shows too many false alerts.
