"""HTTP service around the case workflow (FastAPI).

Run (demo, no API key, replays recorded Gemini notes):
    .venv/bin/uvicorn screening.service:app_from_env --factory --port 8000

Endpoints
  GET  /health                       data loaded? which mode/model?
  GET  /demo/cases                   the 38 recorded demo alerts (demo mode only)
  POST /cases                        start a case: {"demo_case_id": "N05"} (demo) or
                                     {"customer": {...}, "documents": [...]} (live)
  GET  /cases/{case_id}              review payload while paused, or the final status
  POST /cases/{case_id}/decision     {"disposition": "cleared|escalated", "reviewer", "reason"}
  GET  /cases/{case_id}/audit        this case's audit trail
  GET  /audit/verify                 hash-chain check over the whole log
  POST /lookup                       ad-hoc name check: {"full_name", "date_of_birth"?, "nationality"?}.
                                     Not a screening of record (no case is opened), but it is
                                     audit-logged like everything else.

Design notes:
- No endpoint lets anything but a reviewer's decision close a case, and there is no
  endpoint to edit or delete audit rows.
- The service never returns the customer's DOB, id or raw documents: the review payload
  is built from the minimised CaseContext, like the prompt.
- Demo mode uses the eval salt and the frozen eval cases, so prompts are byte-identical to
  the recorded ones and replay works without any key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

from screening.audit import AuditLog
from screening.context import CustomerDocument
from screening.matching import Matcher
from screening.models import Customer, SanctionsEntry
from screening.workflow import (
    ReviewDecision,
    build_workflow,
    pending_review,
    sqlite_checkpointer,
    start_case,
    submit_decision,
)

ROOT = Path(__file__).resolve().parents[1]


class Lookup(BaseModel):
    full_name: str
    date_of_birth: date | None = None
    nationality: str | None = None


class StartCase(BaseModel):
    demo_case_id: str | None = None
    customer: Customer | None = None
    documents: list[CustomerDocument] = []


def create_app(
    *,
    entries: list[SanctionsEntry],
    llm: Any,
    salt: bytes,
    data_dir: Path,
    mode: str,
    demo_cases: list[dict] | None = None,
    list_snapshot: str | None = None,
) -> FastAPI:
    data_dir.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(data_dir / "audit.sqlite")
    matcher = Matcher(entries)
    by_id = {e.entry_id: e for e in entries}
    workflow = build_workflow(
        matcher, by_id, llm, salt,
        checkpointer=sqlite_checkpointer(str(data_dir / "cases.sqlite")),
        audit=audit, list_snapshot=list_snapshot,
    )
    demo = {c["case_id"]: c for c in (demo_cases or [])}
    api = FastAPI(title="Sanctions screening", version="1.0")

    def cfg(case_id: str) -> dict:
        return {"configurable": {"thread_id": case_id}}

    def values(case_id: str) -> dict:
        state = workflow.get_state(cfg(case_id))
        if not state.values:
            raise HTTPException(404, "unknown case")
        return state.values

    @api.get("/health")
    def health() -> dict:
        return {"ok": True, "mode": mode, "model": getattr(llm, "model_name", None),
                "list_entries": len(entries), "list_snapshot": list_snapshot}

    @api.get("/demo/cases")
    def demo_cases_list() -> list[dict]:
        if mode != "demo":
            raise HTTPException(404, "demo cases only exist in demo mode")
        return [{"case_id": c["case_id"], "group": c["group"], "customer_name": c["customer"]["full_name"],
                 "has_document": bool(c["documents"])} for c in demo.values()]

    @api.post("/cases")
    def start(body: StartCase) -> dict:
        if mode == "demo":
            if body.demo_case_id not in demo:
                raise HTTPException(422, "demo mode: pass a demo_case_id from /demo/cases")
            c = demo[body.demo_case_id]
            customer = Customer.model_validate(c["customer"])
            documents = [CustomerDocument(**d) for d in c["documents"]]
        else:
            if body.customer is None:
                raise HTTPException(422, "live mode: pass a customer")
            customer, documents = body.customer, body.documents
        case_id = f"case-{uuid.uuid4().hex[:12]}"
        state = start_case(workflow, case_id, customer, documents)
        return {"case_id": case_id, "case_ref": state["case_ref"], "status": state["status"],
                "review": pending_review(workflow, case_id)}

    @api.get("/cases/{case_id}")
    def get_case(case_id: str) -> dict:
        v = values(case_id)
        out = {"case_id": case_id, "case_ref": v.get("case_ref"), "status": v.get("status"),
               "review": pending_review(workflow, case_id)}
        if v.get("decision") is not None:
            out["decision"] = v["decision"].model_dump(mode="json")
        return out

    @api.post("/cases/{case_id}/decision")
    def decide(case_id: str, decision: dict) -> dict:
        values(case_id)  # 404 if unknown
        if pending_review(workflow, case_id) is None:
            raise HTTPException(409, "case is not waiting for a decision")
        try:
            ReviewDecision.model_validate(decision)
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json())) from exc
        final = submit_decision(workflow, case_id, decision)
        return {"case_id": case_id, "status": final["status"]}

    @api.get("/cases/{case_id}/audit")
    def case_audit(case_id: str) -> list[dict]:
        ref = values(case_id).get("case_ref")
        return [{"seq": r.seq, "step": r.step, "at": r.at, "event": r.event}
                for r in audit.case_history(ref, run_id=case_id)]

    @api.post("/lookup")
    def lookup(body: Lookup) -> dict:
        run_id = f"lookup-{uuid.uuid4().hex[:12]}"
        customer = Customer(customer_id=run_id, full_name=body.full_name,
                            date_of_birth=body.date_of_birth, nationality=body.nationality)
        result = matcher.screen(customer)
        # Keyed hash of the queried name: the log proves a lookup happened and what it found
        # without storing the name itself.
        name_ref = hmac_ref(body.full_name)
        audit.append({"step": "adhoc_lookup", "case_ref": "ADHOC", "run_id": run_id,
                      "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "name_ref": name_ref, "alert": result.alert, "list_snapshot": list_snapshot,
                      "top_entries": [c.entry_id for c in result.candidates[:5]]})
        return {"run_id": run_id, "alert": result.alert, "threshold": result.threshold,
                "note": "Ad-hoc lookup, not a screening of record. Open a case to review an alert.",
                "candidates": [{
                    "entry_id": c.entry_id, "score": c.score, "matched_on": c.matched_on,
                    "name_source": c.name_source, "dob_check": c.dob_check,
                    "dob_years_apart": c.dob_years_apart, "nationality_check": c.nationality_check,
                    "list_primary_name": by_id[c.entry_id].primary_name,
                    "list_programs": by_id[c.entry_id].programs,
                } for c in result.candidates[:5]]}

    def hmac_ref(text: str) -> str:
        return hmac.new(salt, text.strip().lower().encode(), hashlib.sha256).hexdigest()[:16]

    @api.get("/audit/verify")
    def verify() -> dict:
        broken = audit.verify()
        return {"intact": broken is None, "first_bad_seq": broken}

    return api


def app_from_env() -> FastAPI:
    """SCREENING_MODE=demo (default): replay recorded Gemini notes, no key needed.
    SCREENING_MODE=live: SCREENING_PROVIDER=gemini|claude, CASE_REF_SALT required (>=16 chars),
    model key from the environment or .env. Live calls cost money."""
    mode = os.environ.get("SCREENING_MODE", "demo")
    ofac_dir = Path(os.environ.get("SCREENING_OFAC_DIR", ROOT / "data" / "ofac"))
    data_dir = Path(os.environ.get("SCREENING_DATA_DIR", ROOT / "data" / "service" / mode))
    if not (ofac_dir / "entries.jsonl").exists():
        raise RuntimeError(f"{ofac_dir}/entries.jsonl missing: run `python -m scripts.download_ofac` first")
    with (ofac_dir / "entries.jsonl").open(encoding="utf-8") as fh:
        entries = [SanctionsEntry.model_validate_json(line) for line in fh]
    snapshot = json.loads((ofac_dir / "manifest.json").read_text())["files"]["SDN.CSV"]["sha256"]

    if mode == "demo":
        from evaluation.cassette import ReplayLLM
        from evaluation.run_note_eval import CASES, EVAL_SALT, cassette_path

        cases = json.loads(CASES.read_text(encoding="utf-8"))
        if cases["built_from"]["ofac_snapshot"]["sdn_sha256"] != snapshot:
            raise RuntimeError("demo cases were recorded on a different OFAC snapshot")
        return create_app(entries=entries, llm=ReplayLLM(cassette_path("gemini")), salt=EVAL_SALT,
                          data_dir=data_dir, mode="demo", demo_cases=cases["cases"], list_snapshot=snapshot)

    if mode != "live":
        raise RuntimeError("SCREENING_MODE must be demo or live")
    salt = os.environ.get("CASE_REF_SALT", "").encode()
    if len(salt) < 16:
        raise RuntimeError("live mode needs CASE_REF_SALT (at least 16 characters)")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    from screening.llm import ClaudeLLM, GeminiLLM

    llm = GeminiLLM() if os.environ.get("SCREENING_PROVIDER", "gemini") == "gemini" else ClaudeLLM()
    return create_app(entries=entries, llm=llm, salt=salt, data_dir=data_dir, mode="live", list_snapshot=snapshot)
