"""Generator tests on fictional list entries. No network, no real data."""

import json
import random
from datetime import date

import pytest

from screening.models import SanctionsEntry
from screening.normalize import natural_order, normalize, token_key
from screening.synthetic import (
    CustomerKind,
    Generator,
    Perturbation,
    customer_style,
    drop_token,
    strip_punct,
    parse_list_dob,
    transliterate,
    typo,
)
from scripts.generate_customers import run


def _entries() -> list[SanctionsEntry]:
    out = []
    for i in range(40):
        out.append(
            SanctionsEntry(
                entry_id=str(1000 + i),
                primary_name=f"STORMWING{i}, Muhammad Ivo Talon",
                entity_type="individual",
                aliases=[f"STORMWYNG{i}, Mohammed"],
                dates_of_birth=["14 Mar 1968"] if i % 3 else ["1971"],
                nationalities=["Freedonia"],
            )
        )
    out.append(SanctionsEntry(entry_id="9", primary_name="GREY FINCH LTD"))  # entity: ignored
    out.append(
        SanctionsEntry(
            entry_id="10", primary_name="VAGUE, Person", entity_type="individual",
            dates_of_birth=["circa 1960"],
        )
    )
    return out


# --- normalisation ---------------------------------------------------------------------

def test_normalize_handles_order_case_diacritics_punctuation():
    assert natural_order("QUILLFEATHER, Mårten") == "Mårten QUILLFEATHER"
    assert normalize("QUILLFEATHER, Mårten-Ivo") == "marten ivo quillfeather"
    assert token_key("Ivo QUILLFEATHER") == token_key("QUILLFEATHER, Ivo")


# --- perturbations ---------------------------------------------------------------------

def test_typo_changes_exactly_one_token_and_keeps_first_letter():
    rng = random.Random(1)
    out = typo("Marten Quillfeather", rng)
    a, b = "Marten Quillfeather".split(), out.split()
    changed = [(x, y) for x, y in zip(a, b) if x != y]
    assert len(changed) == 1 and changed[0][0][0] == changed[0][1][0]


def test_perturbations_return_none_when_not_applicable():
    rng = random.Random(1)
    assert drop_token("Ivo Heron", rng) is None
    assert transliterate("Ivo Heron", rng) is None
    assert typo("Al Bo", rng) is None


def test_strip_punct_deletes_apostrophes_and_splits_hyphens():
    assert strip_punct("AL-AHMAD, Sa'ad") == "Saad AL AHMAD"


def test_customer_style_title_cases_keeping_apostrophes():
    assert customer_style("sa'ad AL-AHMAD") == "Sa'ad Al-Ahmad"


def test_letter_case_does_not_reveal_kind():
    """Regression: list-derived names kept OFAC's UPPERCASE surname, clean ones never did."""
    out = Generator(_entries(), seed=2).generate(60)
    for c in out:
        assert c.customer.full_name == customer_style(c.customer.full_name)
        assert not any(t.isupper() and len(t) > 1 for t in c.customer.full_name.split())


def test_transliteration_uses_known_variant():
    out = transliterate("Muhammad Heron", random.Random(3))
    assert out.split()[0].lower() in {"mohammed", "mohamed", "mohammad"}


def test_parse_list_dob_precision():
    assert parse_list_dob("14 Mar 1968") == ("full", date(1968, 3, 14))
    assert parse_list_dob("1971") == ("year", 1971)
    assert parse_list_dob("circa 1960") is None
    assert parse_list_dob("1962 to 1964") is None


# --- generator -------------------------------------------------------------------------

def test_same_seed_same_output_different_seed_differs():
    a = Generator(_entries(), seed=3).generate(50)
    b = Generator(_entries(), seed=3).generate(50)
    c = Generator(_entries(), seed=4).generate(50)
    assert [x.model_dump() for x in a] == [x.model_dump() for x in b]
    assert [x.model_dump() for x in a] != [x.model_dump() for x in c]


def test_mix_and_labels_are_consistent():
    out = Generator(_entries(), seed=1).generate(50)
    kinds = [c.kind for c in out]
    assert kinds.count(CustomerKind.TRUE_MATCH) == 20
    assert kinds.count(CustomerKind.HARD_NEGATIVE) == 10
    assert kinds.count(CustomerKind.CLEAN) == 20
    for c in out:
        if c.kind is CustomerKind.TRUE_MATCH:
            assert c.should_alert and c.is_true_match and c.source_entry_id
        elif c.kind is CustomerKind.HARD_NEGATIVE:
            assert c.should_alert and not c.is_true_match and c.source_entry_id
        else:
            assert not c.should_alert and not c.is_true_match and c.source_entry_id is None


def test_only_individuals_used_and_vague_dob_never_hard_negative():
    out = Generator(_entries(), seed=2).generate(80, true_share=0.5, hard_share=0.5)
    sources = {c.source_entry_id for c in out}
    assert "9" not in sources  # entity
    assert not any(c.source_entry_id == "10" and c.kind is CustomerKind.HARD_NEGATIVE for c in out)


def test_hard_negative_dob_at_least_ten_years_from_every_list_dob():
    entries = {e.entry_id: e for e in _entries()}
    out = Generator(_entries(), seed=5).generate(60)
    for c in out:
        if c.kind is CustomerKind.HARD_NEGATIVE:
            entry = entries[c.source_entry_id]
            years = []
            for raw in entry.dates_of_birth:
                kind, v = parse_list_dob(raw)
                years.append(v.year if kind == "full" else v)
            assert all(abs(c.customer.date_of_birth.year - y) >= 10 for y in years)
            assert c.customer.nationality not in entry.nationalities


def test_year_only_list_dob_gives_customer_date_in_that_year():
    entries = {e.entry_id: e for e in _entries()}
    out = Generator(_entries(), seed=6).generate(60)
    tagged = [c for c in out if Perturbation.LIST_DOB_YEAR_ONLY in c.perturbations]
    assert tagged
    for c in tagged:
        if c.customer.date_of_birth is not None:
            assert c.customer.date_of_birth.year == int(entries[c.source_entry_id].dates_of_birth[0])


def test_clean_names_never_collide_with_list_names():
    entries = _entries() + [
        SanctionsEntry(entry_id="77", primary_name="HARTLEY, Olivia", entity_type="individual")
    ]
    gen = Generator(entries, seed=8)
    out = gen.generate(300, true_share=0.0, hard_share=0.0)
    assert all(token_key(c.customer.full_name) != token_key("HARTLEY, Olivia") for c in out)


def test_ids_do_not_leak_kind():
    out = Generator(_entries(), seed=9).generate(50)
    assert [c.customer.customer_id for c in out] == [f"C{i:05d}" for i in range(1, 51)]
    first_20 = [c.kind for c in out[:20]]
    assert len(set(first_20)) > 1  # shuffled, not blocks of one kind


def test_no_individuals_fails_loudly():
    with pytest.raises(ValueError):
        Generator([SanctionsEntry(entry_id="1", primary_name="X LTD")]).generate(10)


def test_cli_run_writes_customers_and_manifest(tmp_path):
    ofac = tmp_path / "ofac"
    ofac.mkdir()
    (ofac / "entries.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in _entries()))
    (ofac / "manifest.json").write_text(json.dumps({
        "retrieved_at": "2026-11-24T00:00:00+00:00",
        "files": {"SDN.CSV": {"sha256": "aaa"}, "ALT.CSV": {"sha256": "bbb"}},
    }))
    manifest = run(ofac, tmp_path / "out", n=30, seed=1)
    lines = (tmp_path / "out" / "customers.jsonl").read_text().splitlines()
    assert len(lines) == 30
    assert manifest["ofac_snapshot"]["sdn_sha256"] == "aaa"
    assert manifest["summary"]["total"] == 30


def test_lookalikes_recombine_same_nationality_and_never_equal_or_nest_a_list_name():
    entries = [
        SanctionsEntry(entry_id=str(i), primary_name=f"{sur}, {giv}", entity_type="individual",
                       nationalities=["Freedonia"])
        for i, (sur, giv) in enumerate([("STORMWING", "Ivo"), ("HERONSBANE", "Talon"),
                                         ("QUILLFEATHER", "Marten"), ("ASHBROOK", "Petra")])
    ]
    gen = Generator(entries, seed=3)
    out = gen.generate(20, true_share=0.0, hard_share=0.0, lookalike_share=0.5)
    looks = [c for c in out if c.kind is CustomerKind.LOOKALIKE_CLEAN]
    assert len(looks) == 10
    list_keys = [token_key(e.primary_name) for e in entries]
    for c in looks:
        k = token_key(c.customer.full_name)
        assert not c.should_alert and c.source_entry_id is None
        assert c.customer.nationality == "Freedonia"
        assert all(k != lk and not k <= lk and not lk <= k for lk in list_keys)


def test_lookalikes_off_by_default_so_dev_set_is_unchanged():
    out = Generator(_entries(), seed=7).generate(40)
    assert not any(c.kind is CustomerKind.LOOKALIKE_CLEAN for c in out)
