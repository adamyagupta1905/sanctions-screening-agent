# Evaluation: name screening

_Run 2026-11-24 · answer key v1 (seed 2027, 1,000 customers) · OFAC snapshot retrieved
2026-11-24 01:47 UTC (SDN sha256 `89b2a6d6…`) · results file
[`evaluation/results/matching_key-v1_2026-11-24.json`](../evaluation/results/matching_key-v1_2026-11-24.json)_

No language model is involved in this step, so the eval is deterministic, offline and free.
Re-running it on the same snapshot gives the same numbers.

## What is measured

| Customers in the answer key | n | Must be reviewed? | Is the listed person? |
|---|---|---|---|
| True match: a listed individual, name altered (typo, dropped name, transliteration, alias, ...) | 300 | yes | yes |
| Hard negative: same name as a listed person, born 10-25 years apart, other nationality | 150 | yes | no |
| Clean: names unlike the list | 250 | no | no |
| Look-alike clean: given name of listed person A + surname of listed person B, same nationality, not equal to or a sub/superset of any list name | 300 | no | no |

A customer counts as caught only if the alert names **the right list entry**. Alerting on a
different person does not count. The 0.88 threshold was chosen on the separate dev set
(seed 7) and was **not changed** after seeing these results.

## Results

| Metric | Matcher | Exact baseline |
|---|---|---|
| Recall, customers that must be reviewed (450) | **96.9%** | 63.3% |
| Recall, true matches (300) | **95.3%** | 45.0% |
| Missed true matches (the harmful error) | **14** | 165 |
| False alerts, clean (250) | 0 | 0 |
| False alerts, look-alike clean (300) | 35 (11.7%) | 0 |
| Candidates per alert (analyst review load) | 1.13 | 1.00 |
| Hard negatives alerted with DOB mismatch shown | 150/150 | 150/150 |
| True matches where DOB evidence would mislead (mismatch shown) | 0 | 0 |
| Speed | 31 ms per customer | — |

True-match recall by alteration (a customer can have several):

| Alteration | n | Matcher | Baseline |
|---|---|---|---|
| Word order only / any | 300 | 95.3% | 45.0% |
| Punctuation removed | 149 | 95.3% | 39.6% |
| Typo | 111 | 89.2% | 0.0% |
| Dropped name | 74 | 82.4% | 10.8% |
| Strong alias used | 30 | 86.7% | 40.0% |
| Transliteration | 25 | 80.0% | 0.0% |
| List DOB year-only | 23 | 87.0% | 26.1% |

## What improved
- Fuzzy matching closes most of the gap the baseline leaves: 151 fewer missed listed
  people out of 300. The baseline catches none of the typos or transliterations.
- Every alert carries DOB and nationality evidence. All 150 hard negatives show a DOB
  mismatch the reviewer can use to clear them, and on no true match did the evidence point
  the wrong way (0 mismatches). 39 true matches have DOB "unknown" (customer or list DOB
  missing): those need the reviewer to dig further.

## What still fails
- **14 missed true matches.** 13 of 14 combine a dropped name with a typo or
  transliteration, e.g. "Dmitry Mannsurov" for listed "MANSUROV, Dmitri Flerovich":
  patronymic dropped, first name transliterated and surname misspelt. Most score below 0.80 and are not even shown as candidates.
  Per-token fuzzy matching (score each word against the best-matching list word) is the
  likely fix; it must be designed on the dev set and re-measured here, not tuned to these 14.
- **One near miss is transliteration alone:** "Muhammad Nayef Maged" scores 0.872 against
  the listed spelling, just under 0.88. The matcher has no transliteration knowledge by
  design (the generator's table would make the test circular). A phonetic or independent
  variant table, built from a different source, would be the honest way to add it.
- **One alert on the wrong person:** "Ali Adan" alerted, but on a different listed entry
  than the one it was built from. The reviewer would see an alert, but on the wrong file.
- **Workload on look-alikes:** 35 of 300 (11.7%) customers with list-style names who are
  not listed raise an alert. That is analyst time, not a compliance failure, and it is the
  price of the recall gain: the baseline raises none but misses 165 listed people.

## Threshold trade-off (information only, not used to choose)

| Threshold | True-match recall | Missed | Look-alike false alerts | Clean false alerts | Candidates/alert |
|---|---|---|---|---|---|
| 0.80 | 96.0% | 12 | 181 | 3 | 1.90 |
| 0.84 | 95.7% | 13 | 102 | 1 | 1.38 |
| 0.86 | 95.7% | 13 | 61 | 0 | 1.22 |
| **0.88 (used)** | **95.3%** | **14** | **35** | **0** | **1.13** |
| 0.90 | 94.0% | 18 | 16 | 0 | 1.06 |
| 0.92 | 93.0% | 21 | 4 | 0 | 1.04 |
| 0.95 | 69.0% | 93 | 1 | 0 | 1.01 |

Lowering the threshold buys very little recall (2 more listed people at 0.80) for five
times the look-alike alerts. The remaining misses are a matching-method problem, not a
threshold problem.

## Bugs found while building this (each has a regression test)
- **Prefilter could silently drop candidates** (`test_prefilter_never_drops_a_name_that_scores_above_floor`):
  assumed token-set similarity is always >= token-sort; false in about 10% of random pairs.
- **Label leak through letter case** (`test_letter_case_does_not_reveal_kind`): list-derived
  names kept OFAC's UPPERCASE surnames, clean names did not.
- **Live OFAC comments file has 2 columns, spec says 12** (`test_two_column_comments_layout_seen_in_live_file`).

These were caught by dev-set checks and by reading samples, before this held-out run. The
held-out run itself found no new code bugs.

## Limits
- **Synthetic customers, simple alterations.** No script changes (Cyrillic, Arabic), no
  nicknames, no combined given names. Real data is harder; treat these as an upper bound.
- **Labels are strict by construction.** Some true matches ("Ali Adan", built from the
  alias "ADNAN, Ali Ayad" with a word dropped and a typo) would arguably not be caught by any name-only system. They are kept to
  show the limit honestly, not removed to raise the score.
- **One snapshot, one key, one run.** 300 true matches gives roughly ±2.4 points of
  sampling uncertainty on the 95.3% recall (95% interval). Per-alteration numbers on
  23-30 cases are indicative only.
- **Look-alike false-alert rate depends on how look-alikes are built** (recombined list
  names). It is not a real bank's alert rate.
- **The results file records commit `156bb17-dirty`**: the eval code was uncommitted at run
  time; it is committed together with the results.

## How to reproduce
```bash
.venv/bin/python -m scripts.download_ofac          # must give SDN sha256 89b2a6d6...
.venv/bin/python -m evaluation.run_eval
```
A newer OFAC snapshot makes the runner stop (labels point at entry ids of this snapshot).
Rebuild the key with `python -m evaluation.build_answer_key`, bump its version, and
record a new results file rather than overwriting this one.

# Evaluation: untrusted-text defences (deterministic)

_Run 2026-11-24 · `evaluation/injection_cases.json` v1 (24 cases) · results file
`evaluation/results/injection_checks_v1_2026-11-24.json` · no model involved_

| Category | Cases | Flagged by the pattern detector |
|---|---|---|
| Direct ("ignore previous instructions", fake `<system>` tags, "mark as cleared") | 8 | 8 |
| Paraphrased ("the right outcome here is to close the alert") | 4 | **0** |
| Obfuscated (spelled out, leetspeak, French, Base64) | 4 | 1 |
| Benign controls ("ignore the old postal address") | 8 | 0 false flags |
| **Containment**: payload stays inside its escaped `<data>` block when rendered | 24 | 24/24 |

What this shows:
- The detector catches the obvious phrasing and none of the paraphrases. That is expected,
  and it is why it is a flag shown to the reviewer, not a gate. The 8/8 on direct cases is
  optimistic: the patterns and these cases were written together.
- Escaping holds: a payload such as `</data><system>...` is rendered as text inside its block.
- What is **not** measured yet: whether the LLM case note is influenced by these payloads.
  That needs live model calls and comes with the case-note eval (PROGRESS step 8).

# Evaluation: LLM case notes (harness ready, live run pending)

_Cases: `evaluation/note_cases.json` v1 (38 alerts from answer key v1) · scoring rules:
`evaluation/note_scoring.py` · prompt `case-note-v1`_

| Metric | Cases | Template baseline (no LLM) | LLM (Claude or Gemini) |
|---|---|---|---|
| DOB stated correctly | 38 | 38/38 | not run |
| Advises clearing a true match with exact DOB (harmful) | 26 | 0 | not run |
| Recognises instruction-like text in an injected document | 14 | 7/14 (only detector-flagged ones) | not run |
| Harmful notes, any kind | 38 | 0 | not run |

The template can never misstate a fact because it copies them, so it is a strong floor.
Claude has to match it on safety and earn its place on what the template cannot do: read
documents (e.g. recognise the 7 paraphrased/obfuscated injections the detector misses).
**Pilot (2026-11-24, gemini-3.8-flash, 3 true-match cases):** 3/3 notes passed the Python
checks on the first attempt, 3/3 stated the DOB correctly, 0 harmful, about $0.013 in total.
Three cases say nothing about injection resistance or hard negatives; the full 38-case run
has not happened yet and needs explicit approval because it costs money.
