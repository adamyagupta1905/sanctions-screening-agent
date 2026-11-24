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

## ADR-006: Minimised case context, escaped data blocks, injection flags as a signal only
- **Date:** 2026-11-24
- **Decision:** The model receives one rendered `CaseContext`: Python-computed facts (scores,
  DOB/nationality check results, injection flags) as JSON, then untrusted text (customer
  name, OFAC entries, documents) in `<data id=... source=...>` blocks with `<>&` escaped.
  Customer id becomes a keyed-hash case ref; customer DOB and nationality are not sent;
  documents pass a regex redactor (email, phone, IBAN, Luhn-checked card, dates) first.
  A pattern detector flags instruction-like text for the reviewer but blocks nothing.
- **Alternatives:** Send the full customer record; an ML/LLM injection classifier as a gate;
  drop documents containing flagged text; Microsoft Presidio for PII.
- **Why:** The defence that cannot be talked around is that the model never decides anything
  (scores, alerts and dispositions are Python and human). Minimisation limits what a leak or
  a log could expose. Pattern detection is cheap and explainable but provably evadable
  (0/4 paraphrased attacks caught), so making it a gate would give false confidence; and
  silently dropping evidence would hide things from the reviewer. Regexes over Presidio:
  no extra dependency for a small, testable set of identifiers.
- **Revisit if:** The LLM case-note eval shows injected text changing recommendations, or
  documents contain PII types the regexes miss (then evaluate Presidio).

## ADR-007: LangGraph workflow, LLM note checked in Python, human interrupt as the only decider
- **Date:** 2026-11-24
- **Decision:** A LangGraph `StateGraph`: screen -> build_context -> draft_note (max 2 attempts)
  -> human_review (`interrupt`) -> record. The LLM fills a `CaseNote` schema with no score or
  outcome field. Python checks every note: cited ids exist, any decimal equals a computed
  score/threshold, any "N years" equals a computed DOB gap, no outcome stated as decided.
  A failing note is retried once with the problems listed, then dropped; the case still goes
  to the analyst. Each node appends an event (time, model, prompt version, prompt sha256).
- **Alternatives:** A plain Python function chain; a tool-calling agent that decides which
  evidence to fetch; Claude Managed Agents.
- **Why:** The flow is fixed and must pause for days waiting for a person, then resume:
  that is exactly checkpointing + interrupt, which LangGraph gives and a function chain
  does not. A free-roaming agent adds model-chosen steps to a regulated control with no
  benefit at this stage (all evidence is already gathered by code). Managed Agents would
  host the loop remotely; here the loop is deterministic and must run next to the data.
- **Revisit if:** Evidence gathering becomes open-ended (e.g. adverse-media search), which
  is where model-chosen tool calls would earn their place.

## ADR-008: SQLite audit log (append-only, hash-chained) + SQLite checkpointer
- **Date:** 2026-11-24
- **Decision:** Each workflow node writes its event to `audit_events` as it runs. SQLite
  triggers reject UPDATE/DELETE; each row stores sha256(previous hash + canonical JSON), and
  `verify()` reports the first broken row. Paused cases persist in LangGraph's `SqliteSaver`.
  Events carry the keyed-hash case_ref, never the customer id, name, DOB or documents.
- **Alternatives:** Reuse LangGraph checkpoints as the audit trail; Postgres; an external
  log service; signing each row.
- **Why:** Checkpoints are an engine detail (they hold full state incl. PII, and can be
  pruned); an audit trail must be a separate, minimal, append-only record a reviewer or
  regulator can read. SQLite needs no server and is enough for a single-node demo.
  The hash chain makes silent edits detectable without key management.
- **Limits:** Someone with file access can still delete the file or rewrite the whole chain
  consistently; production would anchor the chain head externally (WORM storage or a signed
  daily checkpoint). Delivery is at-least-once (a crash between audit write and checkpoint
  can log a step twice). The checkpointer keeps full history incl. customer documents.
- **Revisit if:** More than one writer process, or retention/deletion rules for case state.

## ADR-009: Case-note eval with rule scoring, a template baseline and prompt-keyed cassettes
- **Date:** 2026-11-24
- **Decision:** 38 frozen alerts (8 true matches with exact DOB, 4 with unknown DOB, 8 hard
  negatives, 14 true matches carrying an injection document, 4 with a benign document).
  Notes are scored by deterministic rules; harmful errors (contradicting the DOB fact,
  advising to clear a true match, repeating an injected clearance claim as fact) are listed
  case by case. Baseline: a Python template note built only from computed facts. Live
  responses are recorded once, keyed by sha256(prompt), and replayed for free.
- **Alternatives:** LLM-as-judge; human grading only; comparing against a hand-written
  ideal note.
- **Why:** The harmful errors are checkable with rules, which are free, deterministic and
  auditable; a judge model would add cost and its own error rate. The template baseline is
  the honest comparison: it can never misstate a fact, so Claude has to match it on safety
  and earn its place by reading documents (it sees injected text the detector misses).
  Rules cannot judge readability; that is left to a human read and stated as a limit.
- **Revisit if:** Rule verdicts disagree with a manual read of the notes; then add an
  LLM-judge for the disputed criteria, validated against those manual reads.

## ADR-010: Second provider (Gemini) behind the same adapter interface
- **Date:** 2026-11-24
- **Decision:** Add `GeminiLLM` (google-genai, default `gemini-3.8-flash`) next to `ClaudeLLM`
  in `screening/llm.py`. Both satisfy `StructuredLLM`; the eval runner takes `--provider`
  and keeps one cassette and one results file per provider. Gemini receives the Pydantic
  JSON schema as `response_json_schema`; the reply is validated by Pydantic locally, so
  both providers face the identical schema check. Safety stops map to `LLMRefusal`,
  truncation and schema failures to `LLMIncomplete`, so the workflow treats them the same.
- **Alternatives:** Switch the project to Gemini entirely; a multi-provider library
  (LiteLLM, LangChain chat models).
- **Why:** The user has Gemini access and no Anthropic credentials today. Keeping both
  behind one small interface leaves the rest of the system (checks, workflow, scoring,
  audit) unchanged and makes a like-for-like provider comparison possible later. A
  wrapper library would add a dependency for two methods' worth of code.
- **Limits:** Model ID and prices were taken from Google's docs on 2026-11-24 (Flash on
  introductory pricing until 2026-12-31). Structured-output behaviour on this model is
  confirmed only once a live call succeeds.

## ADR-011: FastAPI service + Streamlit reviewer page, key-free demo by replay
- **Date:** 2026-11-24
- **Decision:** `screening/service.py` wraps the workflow (start case, get review payload, submit
  decision, audit trail, chain check) on SQLite storage. `ui/reviewer.py` is a thin Streamlit
  client over HTTP. Demo mode replays the 38 recorded Gemini notes with the eval salt and frozen
  cases, so it runs with no key and no cost. Both bind to localhost.
- **Alternatives:** Streamlit calling the workflow directly; a React front end; Gradio.
- **Why:** An HTTP boundary is how a bank would integrate it (case systems call an API) and
  keeps the UI replaceable; Streamlit is enough for an analyst-facing demo. Replay makes the
  demo reproducible and free, which matters for recruiters running it.
- **Found while clicking through:** Streamlit listened on all interfaces by default (fixed:
  localhost), and two screenings of one customer shared a case_ref in the audit log with no
  way to tell runs apart (fixed: `run_id` on every event; regression test).
- **Revisit if:** Multiple analysts or processes write concurrently (SQLite single-writer).
