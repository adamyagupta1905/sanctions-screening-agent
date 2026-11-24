"""MCP server tests: tools run through the MCP layer against an in-process service."""

import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from screening import mcp_server
from screening.service import create_app
from tests.fakes import ScriptedLLM
from tests.test_service import DEMO
from tests.test_workflow import DECISION, ENTRIES, GOOD, SALT


@pytest.fixture
def api(tmp_path):
    app = create_app(entries=list(ENTRIES.values()), llm=ScriptedLLM([GOOD]), salt=SALT,
                     data_dir=tmp_path, mode="demo", demo_cases=DEMO, list_snapshot="sha")
    client = TestClient(app)
    mcp_server.set_client(client)
    yield client, tmp_path
    mcp_server.set_client(None)


def call(name, **args):
    result = asyncio.run(mcp_server.server.call_tool(name, args))
    assert not result.is_error, result
    return json.loads(result.content[0].text)


def test_every_tool_is_read_only_and_none_can_decide():
    tools = asyncio.run(mcp_server.server.list_tools())
    names = {t.name for t in tools}
    assert names == {"screen_name", "get_case", "get_case_audit", "verify_audit_log", "service_status"}
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools)
    forbidden = ("decision", "decide", "submit", "clear", "escalate", "delete", "update", "start")
    assert not any(word in n for n in names for word in forbidden)


def test_screen_name_finds_listed_person_and_is_audited_without_the_name(api):
    _, tmp_path = api
    out = call("screen_name", full_name="Marten Quillfeather", date_of_birth="1968-03-14")
    assert out["alert"] and out["candidates"][0]["entry_id"] == "42"
    assert out["candidates"][0]["dob_check"] == "exact"
    rows = sqlite3.connect(tmp_path / "audit.sqlite").execute(
        "SELECT payload FROM audit_events WHERE step='adhoc_lookup'").fetchall()
    assert len(rows) == 1 and "Quillfeather" not in rows[0][0]


def test_screen_name_rejects_bad_date(api):
    out = call("screen_name", full_name="Marten Quillfeather", date_of_birth="14/03/1968")
    assert out["error"] == "invalid input"


def test_read_case_audit_and_chain(api):
    client, _ = api
    cid = client.post("/cases", json={"demo_case_id": "N01"}).json()["case_id"]
    pending = call("get_case", case_id=cid)
    assert pending["review"]["note"]["summary"] == GOOD.summary
    client.post(f"/cases/{cid}/decision", json=DECISION)  # the human decides, via the API/UI
    assert call("get_case", case_id=cid)["decision"]["disposition"] == "cleared"
    audit = call("get_case_audit", case_id=cid)
    assert audit["case_id"] == cid and [r["step"] for r in audit["events"]][-1] == "record"
    assert call("verify_audit_log") == {"intact": True, "first_bad_seq": None}
    assert call("service_status")["mode"] == "demo"


def test_unknown_case(api):
    assert call("get_case", case_id="nope") == {"error": "not found"}
