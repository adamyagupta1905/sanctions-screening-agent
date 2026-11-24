"""Audit log: append-only, tamper-evident; workflow persistence across a restart."""

import json
import sqlite3
from datetime import date

import pytest

from screening.audit import AuditLog
from screening.matching import Matcher
from screening.workflow import build_workflow, pending_review, sqlite_checkpointer, start_case, submit_decision
from tests.fakes import ScriptedLLM
from tests.test_workflow import DECISION, ENTRIES, GOOD, HIT, SALT

from screening.context import CustomerDocument, case_ref
from screening.models import Customer


def ev(ref="CASE-1", step="screen", **kw):
    return {"case_ref": ref, "step": step, "at": "2026-11-24T00:00:00+00:00", **kw}


# --- the log itself ---------------------------------------------------------------------

def test_append_and_history_in_order(tmp_path):
    log = AuditLog(tmp_path / "a.sqlite")
    log.append(ev(step="screen", score=0.9))
    log.append(ev(ref="CASE-2"))
    log.append(ev(step="record", disposition="cleared"))
    hist = log.case_history("CASE-1")
    assert [r.step for r in hist] == ["screen", "record"]
    assert hist[0].event["score"] == 0.9
    assert log.verify() is None


@pytest.mark.parametrize("missing", ["case_ref", "step", "at"])
def test_event_without_required_field_rejected(tmp_path, missing):
    e = ev()
    del e[missing]
    with pytest.raises(ValueError):
        AuditLog(tmp_path / "a.sqlite").append(e)


def test_update_and_delete_blocked_by_triggers(tmp_path):
    path = tmp_path / "a.sqlite"
    AuditLog(path).append(ev())
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit_events SET step='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit_events")


def test_direct_edit_bypassing_triggers_is_detected(tmp_path):
    path = tmp_path / "a.sqlite"
    log = AuditLog(path)
    for i in range(4):
        log.append(ev(step=f"s{i}", reviewer="analyst-7"))
    log.close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER audit_no_update")
    forged = json.dumps({**ev(step="s2"), "reviewer": "someone-else"}, sort_keys=True, separators=(",", ":"))
    conn.execute("UPDATE audit_events SET payload=? WHERE seq=3", (forged,))
    conn.commit()
    assert AuditLog(path).verify() == 3


def test_deleted_row_breaks_the_chain(tmp_path):
    path = tmp_path / "a.sqlite"
    log = AuditLog(path)
    for i in range(3):
        log.append(ev(step=f"s{i}"))
    log.close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER audit_no_delete")
    conn.execute("DELETE FROM audit_events WHERE seq=2")
    conn.commit()
    assert AuditLog(path).verify() == 3


# --- workflow + audit + persistence -----------------------------------------------------

def _app(tmp_path, script, audit):
    return build_workflow(Matcher(list(ENTRIES.values())), ENTRIES, ScriptedLLM(script), SALT,
                          checkpointer=sqlite_checkpointer(str(tmp_path / "cases.sqlite")),
                          audit=audit, list_snapshot="sha256-of-sdn")


def test_case_survives_restart_and_full_history_is_audited(tmp_path):
    audit = AuditLog(tmp_path / "audit.sqlite")
    docs = [CustomerDocument(source="kyc_note", text="Email marten@example.test, born 1990-05-05.")]
    start_case(_app(tmp_path, [GOOD], audit), "case-1", HIT, docs)

    # "Restart": brand-new app object and connections, same files.
    audit2 = AuditLog(tmp_path / "audit.sqlite")
    app2 = _app(tmp_path, [], audit2)
    assert pending_review(app2, "case-1") is not None
    assert submit_decision(app2, "case-1", DECISION)["status"] == "cleared"

    ref = case_ref(HIT.customer_id, SALT)
    hist = audit2.case_history(ref)
    assert [r.step for r in hist] == ["screen", "build_context", "draft_note", "human_review", "record"]
    assert hist[0].event["list_snapshot"] == "sha256-of-sdn"
    assert hist[3].event["reason"] == DECISION["reason"]
    assert audit2.verify() is None


def test_audit_log_holds_no_raw_customer_identifiers(tmp_path):
    audit = AuditLog(tmp_path / "audit.sqlite")
    app = _app(tmp_path, [GOOD], audit)
    docs = [CustomerDocument(source="kyc_note", text="Email marten@example.test, born 1990-05-05.")]
    start_case(app, "case-2", HIT, docs)
    submit_decision(app, "case-2", DECISION)
    dump = "\n".join(r[0] for r in sqlite3.connect(tmp_path / "audit.sqlite").execute("SELECT payload FROM audit_events"))
    for secret in ['"C1"', "1990", "marten@example.test", HIT.full_name]:
        assert secret not in dump, secret


def test_no_alert_screening_is_still_audited(tmp_path):
    audit = AuditLog(tmp_path / "audit.sqlite")
    clean = Customer(customer_id="C9", full_name="Olivia Hartley", date_of_birth=date(1980, 1, 1))
    start_case(_app(tmp_path, [], audit), "case-3", clean)
    hist = audit.case_history(case_ref("C9", SALT))
    assert [r.step for r in hist] == ["screen"] and hist[0].event["alert"] is False
