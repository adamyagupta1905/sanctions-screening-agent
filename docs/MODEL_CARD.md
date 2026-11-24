# Model card: sanctions screening with LLM case notes

_Version: repo `main`, 2026-11-24 · OFAC SDN snapshot retrieved 2026-11-24 (SDN.CSV sha256
`89b2a6d6…`) · matcher threshold 0.88 · case-note prompt `case-note-v1` · note model
`gemini-3.8-flash` (Claude adapter built, never run live)_

Every number below comes from a committed file in `evaluation/results/`; `tests/test_model_card.py`
fails if this card and those files disagree.

## What it is
A decision-support system for a bank's sanctions-screening analysts. Code compares a customer
with the US OFAC SDN list and raises an alert; a language model drafts a short, cited case note;
a human analyst clears or escalates; every step goes into a tamper-evident audit log.

| Part | Who/what | Can it decide the outcome? |
|---|---|---|
| Name matching, DOB and nationality checks | Deterministic Python (rapidfuzz) | No: it raises alerts |
| Case note | LLM, behind a minimised context; every note checked by Python | No: no schema field for it |
| Clear or escalate | Human analyst | **Yes, only here** |
| MCP tools for AI assistants | Read-only | No |

## Intended use
- Portfolio demonstration of a controlled LLM workflow in a regulated process.
- Analyst decision support: surfacing why an alert fired and what to check next.

## Out of scope (do not use for)
- Automatically clearing or escalating alerts, or any decision without a human.
- Real customer data. Everything was built and tested on synthetic customers.
- Lists other than OFAC SDN, entity/vessel/aircraft screening (customers are matched
  against individuals only), or transaction screening.
- Production compliance without independent model validation, tuning on the bank's own
  alert history, and the hardening listed under Limitations.

## Data
- **Sanctions list:** public OFAC SDN legacy CSV files, kept as published (DOBs as raw text).
  19,629 entries, of which 7,573 individuals.
- **Customers:** synthetic, built by rule from list entries so every label is true by
  construction: true matches (names altered by typo, dropped name, transliteration, alias),
  hard negatives (same name, born 10+ years apart), clean names, and look-alike names
  (recombined from listed people of the same nationality, not on the list).
- **Held-out answer key:** 1,000 customers, seed 2027; the threshold was chosen on a separate
  development seed and never tuned on this key.

## Evaluation

**Name matching** (answer key v1, `matching_key-v1_2026-11-24.json`)

| Metric | Matcher | Exact-match baseline |
|---|---|---|
| Recall, customers that must be reviewed (450) | 96.9% | 63.3% |
| Recall, true matches (300) | 95.3% | 45.0% |
| Missed listed people (harmful) | 14 | 165 |
| False alerts, look-alike names (300) | 35 | 0 |
| False alerts, clean names (250) | 0 | 0 |
| Speed | 31.7 ms per customer | |

**Case notes** (38 alerts, live on `gemini-3.8-flash`, 40 calls, about $0.18;
`case_notes_gemini_replay_2026-11-24.json`)

| Metric | Gemini | Template note (no LLM) |
|---|---|---|
| Harmful notes (contradicts a fact, advises clearing a true match, follows an injection) | 0 / 38 | 0 / 38 |
| DOB stated correctly | 38 / 38 | 38 / 38 |
| Injected documents recognised | 14 / 14 | 7 / 14 |
| Benign documents flagged as possible injection | 1 / 4 | 0 / 4 |

**Untrusted-text defences** (`injection_checks_v1_2026-11-24.json`): payloads stay inside their
escaped data block in 24 / 24 cases. The pattern detector alone catches 8 / 8 direct, 0 / 4
paraphrased and 1 / 4 obfuscated attacks, which is why it is only a signal to the analyst.

## Who bears the false alerts
Look-alike customers (not on the list) by nationality, at threshold 0.88
(`false_alerts_by_nationality_key-v1.json`; groups under 15 people are too small to read):

| Nationality | False alerts | Rate |
|---|---|---|
| Russia | 14 / 86 | 16% |
| Iran | 1 / 36 | 3% |
| Mexico | 1 / 31 | 3% |
| Clean customers (Japan, Brazil, Poland, ...) | 0 / 250 | 0% |

False alerts fall on customers whose names follow the naming conventions most common on the
list. For them this means delays and extra questions; for the bank, analyst time. Name-only
alerting is deliberate (it avoids hiding hits where the list DOB is missing), so the mitigation
is in review: the case note surfaces DOB and nationality evidence first. A bank deploying this
should monitor alert and clearance rates by customer group on real data.

## Risks and mitigations
| Risk | Mitigation in this system |
|---|---|
| A listed person is missed | Recall measured on a held-out key; misses listed individually; threshold fixed in advance |
| The model invents or alters a fact | Python check on every note (cited ids exist, numbers equal computed facts); failing notes dropped |
| The model is manipulated by text in documents | Minimised context, escaped data blocks, schema with no outcome field, human decision; measured on 14 attacks |
| Personal data leaks to the model provider | No customer id, DOB or nationality sent; documents redacted (email, phone, IBAN, card, dates) |
| Decisions cannot be reconstructed | Hash-chained, append-only audit log with list snapshot, model, prompt version and hash, reviewer, reason, run id |
| An AI agent closes an alert | No API path or MCP tool decides; only the analyst form does |

## Limitations
- Synthetic customers with simple name alterations (no script changes, nicknames or
  combined names): real data will be harder. Treat results as an upper bound.
- One run, small samples (300 true matches; 38 notes; per-group rates on 31-86 people).
- Case-note scoring rules were corrected after reading Gemini's notes; a run on a model they
  have not seen (e.g. Claude) is the honest out-of-sample test and has not been done.
- Of the 14 missed listed people, 13 combine a dropped name with a typo or transliteration;
  the matcher has no transliteration knowledge by design (it would make the eval circular).
- The redactor is regex-based and was tested only on synthetic text.
- The audit chain is not anchored outside the machine; SQLite storage is single-node.
- Gemini Flash prices used here are introductory (until 2026-12-31).

## Maintenance
Re-run all evals (`CLAUDE.md`, Commands) whenever the OFAC snapshot, matcher, threshold,
prompt (`PROMPT_VERSION`) or model changes. A new OFAC snapshot requires rebuilding the answer
key with a new version number; old results stay in `evaluation/results/` for comparison.
