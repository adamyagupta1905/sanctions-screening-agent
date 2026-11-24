# Sanctions Screening Agent

Banks must screen every customer against sanctions lists. Fuzzy name matching produces
many false positives, and analysts spend hours writing up each alert. This project
automates the write-up, not the decision:

- **Python** normalises names and scores matches against the public OFAC SDN list.
- **An LLM agent** gathers the evidence and drafts a case note where every claim is cited.
- **A human reviewer** clears or escalates each case, and every step is audit-logged.

All customers are synthetic. Only public sanctions data is used.

## Results

Name screening on a held-out set of 1,000 synthetic customers against the real OFAC list
(full write-up, failures and limits: [docs/EVALUATION.md](docs/EVALUATION.md)):

| Metric | Exact-match baseline | Matcher |
|---|---|---|
| Recall, true matches (300) | 45.0% | **95.3%** |
| Missed listed people (harmful) | 165 | **14** |
| False alerts, look-alike names not on the list (300) | 0 | 35 |
| False alerts, clean names (250) | 0 | 0 |
| Hard negatives with DOB mismatch shown to reviewer | 150/150 | 150/150 |

Not built yet: LLM case notes (citation validity, faithfulness) and prompt-injection tests.

## Quick start

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # offline, no API key needed
cp .env.example .env                   # only needed for live runs; never commit .env
```

## Status

Ingestion, synthetic data, matching and its evaluation are built; the LLM agent is next. See [PROGRESS.md](PROGRESS.md) for the build plan and [docs/DECISIONS.md](docs/DECISIONS.md) for design decisions.
