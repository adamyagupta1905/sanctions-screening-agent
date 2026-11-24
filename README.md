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

LLM case notes on 38 alerts (live, `gemini-3.8-flash`, about $0.18), against a no-LLM template note:

| Metric | Template note | Gemini |
|---|---|---|
| Harmful notes (contradicts a fact, advises clearing a true match, follows an injection) | 0/38 | **0/38** |
| Injected documents recognised (incl. paraphrased, obfuscated) | 7/14 | **14/14** |
| Notes passing the workflow's Python checks first time | 38/38 | 38/38 |

Both evals also found bugs in my own checks; they are listed in the write-up.

## Quick start

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q                 # offline, no API key needed
.venv/bin/python -m scripts.download_ofac     # public OFAC list into data/ (needed for the demo)
```

Demo (no API key, no cost: replays recorded model notes), in two terminals:

```bash
.venv/bin/uvicorn screening.service:app_from_env --factory --host 127.0.0.1 --port 8000
.venv/bin/streamlit run ui/reviewer.py --server.address localhost --server.port 8501
```

Open http://localhost:8501, pick an alert, read the note, clear or escalate, see the audit trail.
The demo needs the same OFAC snapshot the notes were recorded on (sha256 in `evaluation/note_cases.json`);
a newer list download makes the demo refuse to start rather than replay mismatched cases.

## Status

Ingestion, matching, the LangGraph workflow with LLM case notes, the audit log and both evaluations are built. See [PROGRESS.md](PROGRESS.md) for the build plan and [docs/DECISIONS.md](docs/DECISIONS.md) for design decisions.
