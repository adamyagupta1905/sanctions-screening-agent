"""The model card must agree with the committed results files (no hand-copied stale numbers)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = (ROOT / "docs" / "MODEL_CARD.md").read_text(encoding="utf-8")
RES = ROOT / "evaluation" / "results"


def load(name):
    return json.loads((RES / name).read_text(encoding="utf-8"))


def pct(x):
    return f"{100 * x:.1f}%"


def test_matching_numbers():
    r = load("matching_key-v1_2026-11-24.json")
    m, b = r["matcher"]["metrics"], r["baseline"]["metrics"]
    assert f"| {pct(m['recall_should_alert'])} | {pct(b['recall_should_alert'])} |" in CARD
    assert f"| {pct(m['recall_true_match'])} | {pct(b['recall_true_match'])} |" in CARD
    assert f"| Missed listed people (harmful) | {m['missed_true_matches']} | {b['missed_true_matches']} |" in CARD
    assert f"look-alike names (300) | {m['false_alerts_lookalike']['alerted']} |" in CARD
    assert f"{r['matcher']['ms_per_customer']} ms per customer" in CARD
    assert f"Of the {m['missed_true_matches']} missed listed people" in CARD


def test_case_note_numbers():
    r = load("case_notes_gemini_replay_2026-11-24.json")
    g, t = r["llm"]["summary"]["by_group"], r["baseline"]["summary"]["by_group"]
    assert f"| {g['ALL']['harmful_notes']} / {g['ALL']['n']} | {t['ALL']['harmful_notes']} / {t['ALL']['n']} |" in CARD
    inj, tinj = g["injected"]["mentions_injection"], t["injected"]["mentions_injection"]
    assert f"| {inj['true']} / {inj['n']} | {tinj['true']} / {tinj['n']} |" in CARD
    ben = g["benign_doc"]["mentions_injection"]
    assert f"| {ben['true']} / {ben['n']} |" in CARD
    live = load("case_notes_gemini_live_2026-11-24.json")
    assert f"{live['model_calls']} calls" in CARD and live["model"] in CARD


def test_injection_and_group_numbers():
    inj = load("injection_checks_v1_2026-11-24.json")
    assert inj["containment_ok"].replace("/", " / ") in CARD
    groups = load("false_alerts_by_nationality_key-v1.json")
    for g in groups["by_nationality"]:
        if g["interpretable"]:
            assert f"| {g['nationality']} | {g['false_alerts']} / {g['n']} | {round(100 * g['rate'])}% |" in CARD
    c = groups["clean_total"]
    assert f"| {c['false_alerts']} / {c['n']} |" in CARD
