"""Governed Refund Agent - demo, human review queue, and audit log.
Run:  streamlit run src/webapp/streamlit_app.py
"""
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from webapp import audit, pipeline  # noqa: E402

st.set_page_config(page_title="Governed Refund Agent", layout="wide")

CATEGORIES = ["fashion", "home", "books", "toys", "electronics", "innerwear", "grocery", "personalized", "gift_card"]
TRACKING = ["delivered", "lost", "returned_to_origin", "in_transit"]
LLM_OK = bool(os.environ.get("LLM_MODEL")) and bool(os.environ.get("LLM_API_KEY"))
KEYWORD_LABEL = "Keyword intake (demo, no LLM)"
MODES = {KEYWORD_LABEL: "keyword", "Manual reason (no AI)": "manual"}
if LLM_OK:
    MODES = {"LLM intake (OpenAI-compatible, e.g. Ollama)": "llm", **MODES}
BLANK = "(blank form)"
DEFAULTS = dict(msg="", order_exists=True, belongs=True, category="home", item_price=1000, shipping_fee=49,
                tracking="delivered", days=3, photos=0, r90=0, v30=0, flagged=False,
                manual_reason="damaged", expected=None, last=None)
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)
st.session_state.setdefault("sid", uuid.uuid4().hex[:12])

# Each browser session gets its own log and review queue, so visitors never see each other's data.
# Set PERSISTENT_LOG=1 to use the shared file data/runtime/audit_log.jsonl instead (local use).
PERSIST = os.environ.get("PERSISTENT_LOG") == "1"
LOG = audit.LOG if PERSIST else Path(tempfile.gettempdir()) / f"refund_demo_{st.session_state.sid}.jsonl"


def append_log(event):
    return audit.append(event, path=LOG)


@st.cache_data
def load_examples():
    path = ROOT / "data" / "eval" / "cases_v1.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        c = json.loads(line)
        out[f"{c['case_id']} · {c['type']} · {c['customer_message'][:55]}"] = c
    return out


@st.cache_data
def clause_text():
    return pipeline.load_clauses()


@st.cache_resource
def get_client():
    from agents.baseline import make_client
    return make_client()


EXAMPLES = load_examples()


def load_example():
    label = st.session_state.example
    if label == BLANK:
        for k, v in DEFAULTS.items():
            st.session_state[k] = v
        return
    c = EXAMPLES[label]
    o, cu = c["order"], c["customer"]
    st.session_state.msg = c["customer_message"]
    st.session_state.order_exists = o is not None
    if o:
        st.session_state.belongs = o["belongs_to_customer"]
        st.session_state.category = o["category"]
        st.session_state.item_price = int(o["item_price"])
        st.session_state.shipping_fee = int(o["shipping_fee"])
        st.session_state.tracking = o["tracking_status"]
        st.session_state.days = int(o["days_since_delivery"] or 3)
    st.session_state.photos = c["evidence"]["photos"]
    st.session_state.r90 = cu["refunds_approved_90d"]
    st.session_state.v30 = int(cu["refund_value_30d"])
    st.session_state.flagged = cu["flagged"]
    st.session_state.manual_reason = c["intake"]["reason"]
    st.session_state.expected = c["expected"]
    st.session_state.last = None


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Controls")
    mode_label = st.radio("How is the customer message understood?", list(MODES),
                          index=list(MODES).index(KEYWORD_LABEL))
    mode = MODES[mode_label]
    if mode == "llm":
        st.caption(f"Model: `{os.environ.get('LLM_MODEL', '(set LLM_MODEL in .env)')}`  \n"
                   f"Endpoint: `{os.environ.get('LLM_BASE_URL', 'default')}`")
    if mode == "manual":
        st.selectbox("Reason", pipeline.REASONS, key="manual_reason")
    if not LLM_OK:
        st.caption("LLM intake is switched off in this deployment (no model configured).")
    st.divider()
    auto_approve = st.toggle("Allow automatic approvals", value=True,
                             help="Kill switch. When off, every would-be approval is sent to a human instead.")
    if not auto_approve:
        st.warning("Kill switch ON: all approvals go to human review.")
    st.divider()
    ok, bad = audit.verify(LOG)
    st.caption("Audit log integrity: " + ("✅ intact" if ok else f"❌ broken at entry {bad}"))

st.title("Governed Refund Agent")
st.caption("Prototype on a fictional store with mock data. An AI reads the message; a deterministic rule engine decides. "
           "Please do not enter personal information. Your session's log is private to this browser tab and is not kept permanently.")

tab_new, tab_queue, tab_audit, tab_results = st.tabs(["New request", "Review queue", "Audit log", "Evaluation results"])

# ------------------------------------------------------------------ new request
with tab_new:
    if EXAMPLES:
        st.selectbox("Load a test case (optional)", [BLANK] + list(EXAMPLES), key="example", on_change=load_example)
    left, right = st.columns(2)
    with left:
        st.text_area("Customer message", key="msg", height=130, max_chars=1000)
        a, b = st.columns(2)
        a.checkbox("Order exists in system", key="order_exists")
        b.checkbox("Order belongs to this customer", key="belongs")
        c1, c2, c3 = st.columns(3)
        c1.selectbox("Category", CATEGORIES, key="category")
        c2.number_input("Item price (₹)", min_value=0, step=50, key="item_price")
        c3.number_input("Shipping fee (₹)", min_value=0, step=10, key="shipping_fee")
        d1, d2, d3 = st.columns(3)
        d1.selectbox("Tracking status", TRACKING, key="tracking")
        if st.session_state.tracking == "delivered":
            d2.number_input("Days since delivery", min_value=0, key="days")
        d3.number_input("Photos attached", min_value=0, key="photos")
        e1, e2, e3 = st.columns(3)
        e1.number_input("Approved refunds, last 90 days", min_value=0, key="r90")
        e2.number_input("Refund value, last 30 days (₹)", min_value=0, step=100, key="v30")
        e3.checkbox("Account flagged", key="flagged")
        submit = st.button("Submit request", type="primary", disabled=not st.session_state.msg.strip())

    if submit:
        ss = st.session_state
        order = None
        if ss.order_exists:
            order = {"order_id": "O-DEMO", "belongs_to_customer": ss.belongs, "category": ss.category,
                     "item_price": ss.item_price, "shipping_fee": ss.shipping_fee,
                     "days_since_delivery": ss.days if ss.tracking == "delivered" else None,
                     "tracking_status": ss.tracking}
        customer = {"refunds_approved_90d": ss.r90, "refund_value_30d": ss.v30, "flagged": ss.flagged}
        evidence = {"photos": ss.photos}
        res = pipeline.process(
            ss.msg, order, customer, evidence, mode, manual_reason=ss.manual_reason, auto_approve=auto_approve,
            client=get_client() if mode == "llm" else None, model=os.environ.get("LLM_MODEL"))
        rid = uuid.uuid4().hex[:8]
        append_log({"type": "decision", "request_id": rid, "message": ss.msg, "order": order,
                      "customer": customer, "evidence": evidence, **res})
        ss.last = {"request_id": rid, **res}

    with right:
        last = st.session_state.last
        if last:
            action = last["action"]
            box = {"approve": st.success, "deny": st.error, "escalate": st.warning, "request_info": st.info}[action]
            amt = f" · refund ₹{last['refund_amount']:,.0f}" if last["refund_amount"] is not None else ""
            box(f"**{action.upper().replace('_', ' ')}**{amt}")
            st.write(f"Request `{last['request_id']}` · intake reason: **{last['reason']}** "
                     f"({last['intake_mode']})")
            if last.get("intake_error"):
                st.error(f"Intake failed, so the case was escalated (fail closed): {last['intake_error']}")
            if last.get("kill_switch_applied"):
                st.warning(f"Kill switch applied. The rules would have approved ₹{last.get('proposed_amount')}.")
            st.markdown("**Policy clauses applied**")
            for cid in last["clauses"]:
                with st.expander(cid, expanded=False):
                    st.write(clause_text().get(cid, "Governance control, not a policy clause."))
            exp = st.session_state.expected
            if exp:
                match = exp["action"] == action
                st.caption(f"Test label expects **{exp['action']}** → {'✅ matches' if match else '⚠️ differs'}")
            st.caption("The AI only chose the reason above. The decision came from the rule engine.")
        else:
            st.info("Fill the form or load a test case, then submit.")

# ------------------------------------------------------------------ review queue
with tab_queue:
    pending = audit.pending_reviews(LOG)
    st.subheader(f"Escalated cases waiting for a human ({len(pending)})")
    if not pending:
        st.write("Nothing waiting.")
    for e in pending:
        rid = e["request_id"]
        o = e.get("order") or {}
        with st.container(border=True):
            st.markdown(f"**`{rid}`** · {e['ts']} · reason: `{e.get('reason')}` · clauses: {', '.join(e['clauses'])}")
            st.write(f"“{e['message']}”")
            st.caption(f"Order: {o.get('category')} · price ₹{o.get('item_price')} + shipping ₹{o.get('shipping_fee')} · "
                       f"{o.get('tracking_status')} · days since delivery {o.get('days_since_delivery')} · "
                       f"customer: {e['customer']} · photos: {e['evidence']['photos']}")
            max_amt = float((o.get("item_price") or 0) + (o.get("shipping_fee") or 0))
            r1, r2, r3 = st.columns([1, 1, 2])
            who = r1.text_input("Reviewer", key=f"who_{rid}")
            amt = r2.number_input("Refund amount (₹)", min_value=0.0, max_value=max_amt, value=0.0,
                                  key=f"amt_{rid}", help="Capped at what the customer paid (R-20).")
            note = r3.text_input("Note", key=f"note_{rid}")
            b1, b2 = st.columns(2)
            if b1.button("Approve refund", key=f"ap_{rid}", disabled=not who.strip() or amt <= 0):
                append_log({"type": "human_review", "request_id": rid, "reviewer": who.strip(),
                              "decision": "approve", "refund_amount": amt, "note": note})
                st.rerun()
            if b2.button("Reject", key=f"rj_{rid}", disabled=not who.strip()):
                append_log({"type": "human_review", "request_id": rid, "reviewer": who.strip(),
                              "decision": "reject", "refund_amount": 0, "note": note})
                st.rerun()

# ------------------------------------------------------------------ audit log
with tab_audit:
    entries = audit.read_all(LOG)
    ok, bad = audit.verify(LOG)
    st.subheader(f"Audit log ({len(entries)} entries)")
    st.write("Chain check: " + ("✅ every entry links to the one before it" if ok else f"❌ tampering detected at entry {bad}"))
    if entries:
        df = pd.DataFrame([{
            "time": e["ts"], "type": e["type"], "request": e["request_id"],
            "action/decision": e.get("action") or e.get("decision"),
            "reason": e.get("reason"), "clauses": ", ".join(e.get("clauses", [])),
            "refund": e.get("refund_amount"), "reviewer": e.get("reviewer"),
            "hash": e["hash"][:10]} for e in entries])
        st.dataframe(df.iloc[::-1], hide_index=True)
        st.download_button("Download full log (JSONL)", LOG.read_text(), file_name="audit_log.jsonl")
    else:
        st.write("No entries yet. Submit a request first.")
    st.caption("Tamper-evident, not tamper-proof: editing an old line breaks the hash chain, which this page detects.")

# ------------------------------------------------------------------ results
with tab_results:
    tables = sorted((ROOT / "results").glob("results_table_*.md"))
    if tables:
        for t in tables:
            st.subheader(t.stem.replace("results_table_", "Test set: "))
            st.markdown(t.read_text())
        st.caption("A = LLM decides alone. B = LLM intake + rule engine (this app). C = LLM decides + safety gate. "
                   "Measured with a local Llama 3.1 8B model; see the README for caveats.")
    else:
        st.info("Run the evaluation scripts to generate results/results_table_*.md.")
