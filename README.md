# Sanctions Screening Agent

Banks must screen every customer against sanctions lists. Fuzzy name matching produces
many false positives, and analysts spend hours writing up each alert. This project
automates the write-up, not the decision:

- **Python** normalises names and scores matches against the public OFAC SDN list.
- **An LLM agent** gathers the evidence and drafts a case note where every claim is cited.
- **A human reviewer** clears or escalates each case, and every step is audit-logged.

All customers are synthetic. Only public sanctions data is used.

## Results

| Metric | Exact-match baseline | Pipeline |
|---|---|---|
| Match recall | not run | not run |
| Match precision | not run | not run |
| Case-note citation validity | n/a | not run |
| Prompt-injection cases resisted | n/a | not run |

## Quick start

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # offline, no API key needed
cp .env.example .env                   # only needed for live runs; never commit .env
```

## Status

Scaffold only. See [PROGRESS.md](PROGRESS.md) for the build plan and [docs/DECISIONS.md](docs/DECISIONS.md) for design decisions.
