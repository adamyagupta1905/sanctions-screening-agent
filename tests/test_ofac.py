"""OFAC parser + download script tests. Fictional records in OFAC's layout; no network."""

import hashlib
import json
from datetime import datetime, timezone

import pytest

from screening.ofac import OfacFormatError, parse_remarks, parse_sdn
from scripts.download_ofac import run

SDN = (
    '101,"QUILLFEATHER, Marten","individual","SDGT] [IRGC",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,'
    "\"DOB 03 Feb 1971; alt. DOB circa 1972; POB Nowhere; nationality Freedonia; a.k.a. 'THE HERON'.\"\r\n"
    '102,"BLUE LANTERN TRADING LLC",-0- ,"SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \r\n'
    '103,"SEA WREN","vessel","IRAN",-0- ,"ABCD1","Tanker",-0- ,-0- ,"Freedonia",-0- ,-0- \r\n'
)
ALT = (
    '101,9001,"aka","QUILLFEATHER, Martin",-0- \r\n'
    '102,9002,"fka","BLUE LANTERN TRADE CO",-0- \r\n'
    '999,9003,"aka","NOBODY",-0- \r\n'
)
COMMENTS = '102,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"Website www.example.test."\r\n'


def test_parses_core_fields_and_nulls():
    entries, report = parse_sdn(SDN, ALT)
    by_id = {e.entry_id: e for e in entries}
    p = by_id["101"]
    assert p.primary_name == "QUILLFEATHER, Marten"
    assert p.entity_type == "individual"
    assert p.programs == ["SDGT", "IRGC"]
    assert p.title is None  # "-0- " null marker stays missing
    assert by_id["102"].entity_type is None
    assert report.sdn_rows == 3


def test_remarks_keep_raw_dob_text_and_extract_weak_alias():
    p = {e.entry_id: e for e in parse_sdn(SDN)[0]}["101"]
    assert p.dates_of_birth == ["03 Feb 1971", "circa 1972"]  # ambiguous value kept as written
    assert p.nationalities == ["Freedonia"]
    assert p.weak_aliases == ["THE HERON"]


def test_aliases_joined_and_orphans_reported_not_dropped_silently():
    entries, report = parse_sdn(SDN, ALT)
    by_id = {e.entry_id: e for e in entries}
    assert by_id["101"].aliases == ["QUILLFEATHER, Martin"]
    assert by_id["103"].aliases == []
    assert report.orphan_alt_rows == ["999"]


def test_comment_spillover_appended_to_remarks():
    entries, report = parse_sdn(SDN, ALT, COMMENTS)
    assert {e.entry_id: e for e in entries}["102"].remarks == "Website www.example.test."
    assert report.comment_rows == 1


def test_two_column_comments_layout_seen_in_live_file():
    sdn = '200,"GREY FINCH LTD",-0- ,"SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"Listing Da"\r\n'
    entries, report = parse_sdn(sdn, "", '200,"te: 01 Jan 2020."\r\n')
    assert entries[0].remarks == "Listing Date: 01 Jan 2020."  # joined mid-word, no separator
    assert report.comment_rows == 1


def test_wrong_column_count_fails_loudly():
    with pytest.raises(OfacFormatError, match="columns"):
        parse_sdn('101,"ONLY THREE","individual"\r\n')


def test_non_numeric_ent_num_fails_loudly():
    bad = SDN.replace("103,", "X03,", 1)
    with pytest.raises(OfacFormatError, match="not an integer"):
        parse_sdn(bad)


def test_empty_remarks():
    assert parse_remarks(None) == {"dates_of_birth": [], "nationalities": [], "weak_aliases": []}


def _fake_fetch(files):
    def fetch(url):
        name = url.rsplit("/", 1)[-1]
        if name not in files:
            raise FileNotFoundError(name)
        return files[name]

    return fetch


def test_run_writes_entries_manifest_and_raw_snapshot(tmp_path):
    files = {"SDN.CSV": SDN.encode(), "ALT.CSV": ALT.encode()}  # comments file absent
    now = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    manifest = run(tmp_path, fetch=_fake_fetch(files), min_rows=1, now=now)

    lines = (tmp_path / "entries.jsonl").read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["entry_id"] == "101"
    assert manifest["files"]["SDN.CSV"]["sha256"] == hashlib.sha256(files["SDN.CSV"]).hexdigest()
    assert manifest["files"]["SDN_COMMENTS.CSV"]["status"].startswith("not downloaded")
    assert manifest["counts"]["orphan_alt_rows"] == 1
    assert (tmp_path / "raw" / "20261123T120000Z" / "SDN.CSV").read_bytes() == files["SDN.CSV"]
    assert json.loads((tmp_path / "manifest.json").read_text()) == manifest


def test_run_refuses_suspiciously_small_list(tmp_path):
    files = {"SDN.CSV": SDN.encode(), "ALT.CSV": ALT.encode()}
    with pytest.raises(RuntimeError, match="refusing"):
        run(tmp_path, fetch=_fake_fetch(files), min_rows=1000)
    assert not (tmp_path / "entries.jsonl").exists()


def test_run_fails_if_required_file_missing(tmp_path):
    with pytest.raises(RuntimeError, match="ALT.CSV"):
        run(tmp_path, fetch=_fake_fetch({"SDN.CSV": SDN.encode()}), min_rows=1)


def test_cp1252_bytes_are_decoded_and_recorded(tmp_path):
    sdn = SDN.replace("Marten", "Mårten").encode("cp1252")
    manifest = run(tmp_path, fetch=_fake_fetch({"SDN.CSV": sdn, "ALT.CSV": ALT.encode()}), min_rows=1)
    assert manifest["files"]["SDN.CSV"]["encoding"] == "cp1252"
    assert "Mårten" in (tmp_path / "entries.jsonl").read_text(encoding="utf-8")
