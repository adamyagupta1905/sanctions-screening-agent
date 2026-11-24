"""Analyst review screen (Streamlit). Talks to the FastAPI service over HTTP.

Run:  .venv/bin/streamlit run ui/reviewer.py        (service must be running; see CLAUDE.md)
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

API = os.environ.get("SCREENING_API_URL", "http://localhost:8000")
client = httpx.Client(base_url=API, timeout=120)

st.set_page_config(page_title="Sanctions alert review", layout="wide")
st.title("Sanctions alert review")

try:
    health = client.get("/health").json()
except httpx.HTTPError:
    st.error(f"Screening service not reachable at {API}. Start it first (see CLAUDE.md).")
    st.stop()

with st.sidebar:
    st.caption(f"Mode: **{health['mode']}** · model: `{health['model']}`")
    st.caption(f"List: {health['list_entries']:,} OFAC entries · snapshot `{(health['list_snapshot'] or '')[:12]}…`")
    if health["mode"] == "demo":
        st.info("Demo mode replays recorded model notes: no API key, no cost.")
        cases = client.get("/demo/cases").json()
        labels = {f"{c['case_id']} · {c['customer_name']}" + (" · 📄" if c["has_document"] else ""): c for c in cases}
        choice = st.selectbox("Alert to review", list(labels))
        if st.button("Open alert", type="primary"):
            r = client.post("/cases", json={"demo_case_id": labels[choice]["case_id"]}).json()
            st.session_state["case_id"] = r["case_id"]
            st.session_state["demo_label"] = labels[choice]["group"]

case_id = st.session_state.get("case_id")
if not case_id:
    st.write("Pick an alert in the sidebar and open it.")
    st.stop()

case = client.get(f"/cases/{case_id}").json()
st.subheader(f"Case `{case['case_ref']}` · status: **{case['status']}**")
if "demo_label" in st.session_state:
    with st.expander("Demo answer key (a real analyst never sees this)"):
        st.write(f"Test-set group: `{st.session_state['demo_label']}`")

review = case.get("review")
if review:
    st.markdown("#### Candidates (computed by code, authoritative)")
    st.dataframe(
        [{"id": c["evidence_id"], "list entry": c["entry_id"], "name score": c["score"],
          "matched on": c["name_source"], "DOB": c["dob_check"],
          "DOB gap (yrs)": c["dob_years_apart"], "nationality": c["nationality_check"],
          "programs": ", ".join(c["list_programs"])} for c in review["candidates"]],
        hide_index=True, width="stretch",
    )
    for eid, flags in review["injection_flags"].items():
        st.warning(f"{eid} contains instruction-like text ({', '.join(flags)}). Treat it as unverified data.")

    st.markdown("#### Case note (drafted by the model, checked by code)")
    note = review["note"]
    if note is None:
        st.error("No note: the model's drafts failed the automated checks. Review the evidence directly.")
        for p in review["note_problems"]:
            st.caption(f"• {p}")
    else:
        st.write(note["summary"])
        for c in note["claims"]:
            st.markdown(f"- {c['statement']}  `{', '.join(c['evidence_ids'])}`")
        if note["open_questions"]:
            st.markdown("**Open questions**")
            for q in note["open_questions"]:
                st.markdown(f"- {q}")
        st.markdown(f"**Suggested first step:** {note['recommendation']}")

    st.markdown("#### Your decision")
    if review.get("decision_error"):
        st.error(review["decision_error"])
    with st.form("decision"):
        disposition = st.radio("Outcome", ["escalated", "cleared"], horizontal=True,
                               format_func=lambda d: {"escalated": "Escalate to compliance",
                                                      "cleared": "Clear (false positive)"}[d])
        reviewer = st.text_input("Reviewer id")
        reason = st.text_area("Reason (recorded in the audit log)")
        if st.form_submit_button("Submit decision", type="primary"):
            r = client.post(f"/cases/{case_id}/decision",
                            json={"disposition": disposition, "reviewer": reviewer, "reason": reason})
            if r.status_code == 200:
                st.rerun()
            else:
                detail = r.json().get("detail")
                msgs = [d.get("msg", str(d)) for d in detail] if isinstance(detail, list) else [str(detail)]
                st.error("Decision not accepted: " + "; ".join(msgs))
elif case.get("decision"):
    d = case["decision"]
    st.success(f"Decided: **{d['disposition']}** by `{d['reviewer']}`: {d['reason']}")
else:
    st.info("No alert: nothing to review.")

st.markdown("#### Audit trail")
rows = client.get(f"/cases/{case_id}/audit").json()
st.dataframe([{"#": r["seq"], "step": r["step"], "at": r["at"],
               "details": {k: v for k, v in r["event"].items() if k not in ("step", "at", "case_ref")}}
              for r in rows], hide_index=True, width="stretch")
v = client.get("/audit/verify").json()
st.caption("Audit chain intact ✅" if v["intact"] else f"Audit chain BROKEN at row {v['first_bad_seq']} ❌")
