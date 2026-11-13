"""Trade Authenticity Risk Scorer: Streamlit demonstrator (IS4234 Group 14)."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

import cases
import engine

SGT = ZoneInfo("Asia/Singapore")
SAMPLES = Path(__file__).parent / "sample_docs"
BAND_COLOUR = {"Low": "#2e7d32", "Medium": "#b58900", "High": "#d9730d", "Critical": "#c62828"}
ROLES = ["Trade finance officer (first line)", "Risk Management Unit reviewer (second line)",
         "Board Risk Committee", "Compliance and Internal Audit"]
DEFAULT_NAME = {ROLES[0]: "A. Tan (GTRF)", ROLES[1]: "J. Koh (RMU)", ROLES[2]: "Board Risk Committee",
                ROLES[3]: "L. Wong (Internal Audit)"}
PAID = {"Paid", "Paid (note recorded)", "Released by reviewer"}
PENDING = {"Blocked", "Held", "Held (large silent deal)", "On hold: more information requested"}
OV_OPTIONS = ["Use example table", "confirmed", "unchecked", "contradicted"]
TEXT_FIELDS = ["borrower", "buyer", "product", "invoice_no", "invoice_date", "due_date", "request_date",
               "issuer", "signatory", "vessel", "load_port", "terminal", "doc_date"]
NUM_FIELDS = ["amount", "quantity", "unit_price"]
FIN_FIELDS = {"current_assets": "Current assets", "current_liabilities": "Current liabilities",
              "total_assets": "Total assets", "retained_earnings": "Retained earnings",
              "ebit": "Earnings before interest and tax", "equity": "Equity", "total_liabilities": "Total liabilities"}

st.set_page_config(page_title="Trade Authenticity Risk Scorer", page_icon="🛡️", layout="wide")
ss = st.session_state


# ------------------------------------------------------------------ helpers
def now():
    return datetime.now(SGT)


def ts(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


def money(x):
    return f"US${x:,.0f}" if x < 1e6 else f"US${x / 1e6:,.2f}m"


def badge(band):
    return (f"<span style='background:{BAND_COLOUR[band]};color:white;padding:3px 12px;border-radius:12px;"
            f"font-weight:600'>{band.upper()}</span>")


def log(event, req_id, actor, detail, when=None):
    ss.log.append(dict(Time=ts(when or now()), Event=event, Request=req_id, By=actor, Detail=detail))


@st.cache_data(ttl=3600, show_spinner=False)
def gleif_lookup(name):
    """Live lookup of a legal entity in the public GLEIF LEI register. None if not found or unreachable."""
    try:
        r = requests.get("https://api.gleif.org/api/v1/lei-records",
                         params={"filter[entity.legalName]": name, "page[size]": 1}, timeout=6)
        data = r.json().get("data") or []
        if not data:
            return None
        a = data[0]["attributes"]
        found, asked = a["entity"]["legalName"]["name"].lower(), name.lower()
        if found not in asked and asked not in found:  # ignore loose matches to a different company
            return None
        return dict(lei=a["lei"], name=a["entity"]["legalName"]["name"],
                    country=a["entity"]["legalAddress"].get("country", ""), status=a["entity"].get("status", ""))
    except Exception:
        return None


def alert_email(rec):
    r, q = rec["result"], rec["req"]
    urgent = r["band"] == "Critical"
    lines = [
        f"Request {rec['id']} has been {'BLOCKED' if urgent else 'HELD'} before payout.",
        "",
        f"Borrower: {q['borrower']}",
        f"Buyer: {q['buyer']}",
        f"Amount: {money(q['amount'])}   Facility: {'Silent (buyer not notified)' if q['silent'] else 'Disclosed'}",
        f"Invoice: {q['invoice_no']}   Transport document: {q['doc_type']}",
        f"Submitted by: {rec['officer']} at {rec['submitted']}",
        "",
        f"Risk score: {r['overall']:.0f}/100   Band: {r['band']}",
        f"  Check 1 Documents:   {r['c1']['score']:.0f}",
        f"  Check 2 Company:     {r['c2']['score']:.0f}",
        f"  Check 3 Transaction: {r['c3']['score']:.0f}",
    ]
    if r["overrides"]:
        lines += ["", "Rules applied:"] + [f"  - {o}" for o in r["overrides"]]
    lines += ["", "Top reasons:"] + ([f"  {i + 1}. {x['text']} [{x['source']}]" for i, x in enumerate(r["reasons"][:3])]
                                    or ["  None above threshold."])
    lines += ["", "Could not be checked:"] + ([f"  - {u}" for u in r["unchecked"]] or ["  Nothing."])
    lines += ["", "Recommended action: " + ("Do not release. Verify the transaction through an independent party "
                                             "before any payout." if urgent else
                                             "Review the evidence and decide to release, hold or reject by name.")]
    return dict(to="rmu-alerts@demo-bank.example", time=rec["submitted"], urgent=urgent,
                subject=f"{'[URGENT] ' if urgent else ''}{r['band']} risk: {rec['id']} {q['borrower']} {money(q['amount'])}",
                body="\n".join(lines))


def create_record(req, ev, officer, when=None):
    when = when or now()
    res = engine.score_request(req, ev)
    rid = f"REQ-{len(ss.records) + 1:04d}"
    rec = dict(id=rid, req=req, ev=ev, result=res, status=res["status"], officer=officer, submitted=ts(when),
               note="", decision=None, alert=None)
    ss.records.append(rec)
    log("Request scored", rid, "Scorer (automatic)",
        f"Score {res['overall']:.0f}, band {res['band']}"
        + (f" (raised from {res['base_band']})" if res["band"] != res["base_band"] else "")
        + f". Checks: {res['c1']['score']:.0f} / {res['c2']['score']:.0f} / {res['c3']['score']:.0f}. Status: {res['status']}.",
        when)
    for o in res["overrides"]:
        log("Rule applied", rid, "Scorer (automatic)", o, when)
    if q_silent := req.get("silent"):
        log("Exception recorded", rid, "Scorer (automatic)",
            "Silent deal: buyer acknowledgement of assignment not obtained (ABS Code). Recorded as an exception (R4).", when)
    if res["needs_review"]:
        rec["alert"] = alert_email(rec)
        log("Alert sent", rid, "Scorer (automatic)", f"To {rec['alert']['to']}: {rec['alert']['subject']}", when)
    elif res["status"] == "Paid":
        log("Payment released", rid, "Scorer (automatic)", "Low band: paid as normal.", when)
    return rec


def decide(rec, action, reviewer, reason, when=None):
    new = {"Release": "Released by reviewer", "Hold": "On hold: more information requested", "Reject": "Rejected"}[action]
    rec["status"] = new
    rec["decision"] = dict(action=action, by=reviewer, reason=reason, at=ts(when or now()))
    log(f"Decision: {action.lower()}", rec["id"], reviewer, reason, when)
    if action == "Release":
        log("Payment released", rec["id"], reviewer, "Released after named review.", when)


def add_note(rec, officer, note, when=None):
    rec["note"], rec["status"] = note, "Paid (note recorded)"
    log("Officer note", rec["id"], officer, note, when)
    log("Payment released", rec["id"], officer, "Medium band: paid after note.", when)


def seed():
    ss.records, ss.log = [], []
    t0 = (now() - timedelta(days=3)).replace(hour=9, minute=12, second=0, microsecond=0)
    for i, (f, fin, sig, officer, dec) in enumerate(cases.background_requests()):
        f = dict(f)
        inv_qty = f["quantity"] * (0.9 if f.pop("_mismatch", False) else 1)
        docs = {"Sales contract": dict(buyer=f["buyer"], product=f["product"], quantity=f"{f['quantity']:,.0f}", amount=None),
                "Commercial invoice": dict(buyer=f["buyer"], product=f["product"], quantity=f"{inv_qty:,.0f}", amount=None)}
        req = cases.build_request(dict(f, financials=fin, signals=sig), docs,
                                  dict(status="pass", detail="No modification after creation.", source="Example data: file metadata"))
        when = t0 + timedelta(days=i // 2, hours=5 * (i % 2), minutes=7 * i)
        rec = create_record(req, cases.gather(req), officer, when)
        rec["synthetic"] = True
        if dec:
            if dec[0] == "Note":
                add_note(rec, dec[1], dec[2], when + timedelta(minutes=20))
            else:
                decide(rec, dec[0], dec[1], dec[2], when + timedelta(hours=3))


def reset_form():
    for k in TEXT_FIELDS:
        ss["f_" + k] = ""
    for k in NUM_FIELDS:
        ss["f_" + k] = 0.0
    ss.f_silent, ss.f_doc_type = True, cases.DOC_TYPES[0]
    ss.f_has_accounts = False
    for k in FIN_FIELDS:
        ss["fin_" + k] = 0.0
    for k in engine.SIGNALS:
        ss[f"sig_{k}_recent"], ss[f"sig_{k}_hist"] = 0, ""
    for k in ("registry", "movement", "buyer", "price", "news"):
        ss["ov_" + k] = OV_OPTIONS[0]
    ss.ov_issuer = "Use example table"
    ss.docs, ss.forensics, ss.pack_name, ss.records_note = {}, None, "", ""


def load_pack(data, name):
    fields, docs, forensics = cases.read_pack(data)
    reset_form()
    for k in TEXT_FIELDS:
        ss["f_" + k] = fields.get(k, "") or ""
    for k in NUM_FIELDS:
        ss["f_" + k] = float(fields.get(k) or 0)
    ss.f_silent = bool(fields.get("silent"))
    ss.f_doc_type = fields["doc_type"] if fields.get("doc_type") in cases.DOC_TYPES else cases.DOC_TYPES[0]
    ss.docs, ss.forensics, ss.pack_name = docs, forensics, name
    rec = cases.BORROWER_RECORDS.get(ss.f_borrower.strip().lower())
    if rec:
        ss.f_has_accounts, ss.records_note = True, rec["note"]
        for k in FIN_FIELDS:
            ss["fin_" + k] = float(rec["financials"][k])
        for k in engine.SIGNALS:
            ss[f"sig_{k}_recent"] = int(rec["signals"][k]["recent"])
            ss[f"sig_{k}_hist"] = ", ".join(str(x) for x in rec["signals"][k]["history"])


def cb_load_sample():
    pack = cases.PACKS[ss.sample_key]
    load_pack((SAMPLES / pack["file"]).read_bytes(), pack["file"])


def cb_upload():
    up = ss.get("upl")
    if up is not None:
        try:
            load_pack(up.getvalue(), up.name)
        except Exception as e:  # unreadable file
            ss.upload_error = f"Could not read that file as a request pack ({e})."


def parse_hist(text):
    out = []
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.append(float(part))
            except ValueError:
                pass
    return out


def collect_request():
    fields = {k: ss["f_" + k].strip() for k in TEXT_FIELDS}
    fields.update({k: float(ss["f_" + k]) for k in NUM_FIELDS})
    fields.update(silent=bool(ss.f_silent), doc_type=ss.f_doc_type)
    if ss.f_doc_type != "Bill of lading":
        fields["vessel"] = fields["load_port"] = ""
    if ss.f_doc_type not in ("Inter-tank transfer certificate", "Warehouse receipt"):
        fields["terminal"] = ""
    fields["financials"] = {k: float(ss["fin_" + k]) for k in FIN_FIELDS} if ss.f_has_accounts else None
    fields["signals"] = {k: dict(recent=ss[f"sig_{k}_recent"], history=parse_hist(ss[f"sig_{k}_hist"]))
                         for k in engine.SIGNALS}
    req = cases.build_request(fields, ss.docs, ss.forensics)
    req["records_note"] = ss.records_note
    req["pack_name"] = ss.pack_name
    overrides = {k: ss["ov_" + k] for k in ("registry", "movement", "buyer", "price", "news")}
    overrides["issuer"] = ss.ov_issuer
    return req, overrides


# ------------------------------------------------------------------ result view
def src_tag(source):
    if source.startswith("Live"):
        return "🟢 " + source
    if source.startswith("Example"):
        return "🟡 " + source
    return "⚪ " + source


def render_result(rec, show_email=False):
    r, q = rec["result"], rec["req"]
    a, b, c, d = st.columns([1.1, 1.2, 2, 2])
    a.metric("Risk score", f"{r['overall']:.0f} / 100")
    b.markdown("**Band**<br>" + badge(r["band"]), unsafe_allow_html=True)
    c.markdown(f"**Payment status**<br>{rec['status']}", unsafe_allow_html=True)
    d.markdown(f"**{q['borrower']}** to **{q['buyer']}**<br>{money(q['amount'])}, "
               f"{'silent' if q['silent'] else 'disclosed'}, invoice {q['invoice_no'] or 'n/a'}", unsafe_allow_html=True)
    st.caption("A risk score, not a probability of fraud. It supports the reviewer's judgement and does not prove fraud.")
    if r["band"] != r["base_band"]:
        st.error(f"The score alone gives **{r['base_band']}**. A rule raised the band to **{r['band']}**.")
    for o in r["overrides"]:
        st.warning("Rule applied: " + o)
    st.info("What happens next: " + (r["action"] if not (r["policy_hold"] and r["band"] in ("Low", "Medium"))
                                     else "Held for a named reviewer because it is a large silent deal (R3)."))

    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader(f"Check 1: Documents · {r['c1']['score']:.0f}")
        st.caption("Weight 30%. Issuer failure adds 40, each other failure adds 30.")
        for t in r["c1"]["tests"]:
            icon = {"pass": "✅", "fail": "❌", "unchecked": "❔"}[t["result"]]
            st.markdown(f"{icon} **{t['name']}** (+{t['points']})  \n{t['detail']}  \n<small>{src_tag(t['source'])}</small>",
                        unsafe_allow_html=True)
    with c2:
        st.subheader(f"Check 2: Company · {r['c2']['score']:.0f}")
        st.caption("Weight 30%. Half financial health (Altman Z''), half deviation from the company's own normal.")
        st.markdown(f"**Health:** {r['c2']['health_detail']}  \n<small>{src_tag('Example data: accounts held by the bank')}</small>",
                    unsafe_allow_html=True)
        if q.get("records_note"):
            st.caption(q["records_note"])
        st.markdown(f"**Deviation:** {r['c2']['deviation']:.0f}/100 across five public signals (last 90 days "
                    f"against the past five years).")
        st.dataframe(pd.DataFrame(r["c2"]["signals"]).drop(columns=["note"]).rename(columns={
            "signal": "Signal", "recent": "Last 90 days", "normal": "Its normal", "score": "Score"}),
            hide_index=True, width="stretch")
        odd = [x["signal"] for x in r["c2"]["signals"] if x["normal"] is not None and x["score"] >= 50]
        short = [x["signal"] for x in r["c2"]["signals"] if x["normal"] is None]
        if odd:
            st.markdown("Unusual for this company: " + ", ".join(odd) + ".")
        if short:
            st.markdown("Too little history to judge: " + ", ".join(short) + ".")
        st.markdown(f"<small>{src_tag('Example data: news, ACRA and court records')}</small>", unsafe_allow_html=True)
    with c3:
        st.subheader(f"Check 3: Transaction · {r['c3']['score']:.0f}")
        st.caption("Weight 40%. Agrees = 0, could not check = 50, disagrees = 100. Average of five.")
        for s in r["c3"]["subs"]:
            icon = {"confirmed": "✅", "unchecked": "❔", "contradicted": "❌"}[s["status"]]
            st.markdown(f"{icon} **{s['name']}** ({s['points']})  \n{s['detail']}  \n<small>{src_tag(s['source'])}</small>",
                        unsafe_allow_html=True)

    st.markdown(f"**Overall** = 0.3 × {r['c1']['score']:.0f} + 0.3 × {r['c2']['score']:.0f} + 0.4 × {r['c3']['score']:.0f} "
                f"= **{r['overall']:.1f}**")
    l, m = st.columns(2)
    with l:
        st.markdown("**Top reasons**")
        if r["reasons"]:
            for i, x in enumerate(r["reasons"][:3]):
                st.markdown(f"{i + 1}. {x['text']}")
        else:
            st.markdown("No warning signs found.")
    with m:
        st.markdown("**Could not be checked**")
        if r["unchecked"]:
            for u in r["unchecked"]:
                st.markdown(f"- {u}")
        else:
            st.markdown("Everything was checked.")
    if show_email and rec["alert"]:
        with st.expander("Alert email sent to the Risk Management Unit", expanded=False):
            st.code(f"To: {rec['alert']['to']}\nSent: {rec['alert']['time']}\nSubject: {rec['alert']['subject']}\n\n"
                    + rec["alert"]["body"], language=None)
    if rec["decision"]:
        dcn = rec["decision"]
        st.success(f"Decision: **{dcn['action']}** by **{dcn['by']}** at {dcn['at']}. Reason: {dcn['reason']}")
    if rec["note"]:
        st.success(f"Officer note: {rec['note']}")


def records_table(recs):
    return pd.DataFrame([dict(Request=r["id"], Submitted=r["submitted"], Borrower=r["req"]["borrower"],
                              Buyer=r["req"]["buyer"], Amount=money(r["req"]["amount"]),
                              Silent="Yes" if r["req"]["silent"] else "No", Score=round(r["result"]["overall"]),
                              Band=r["result"]["band"], Status=r["status"], Officer=r["officer"]) for r in recs])


# ------------------------------------------------------------------ state
if "records" not in ss:
    seed()
    reset_form()
    ss.last_id = None
    ss.limit_silent, ss.limit_highrisk = 150.0, 10.0
    ss.sample_key, ss.live_gleif = "genuine", True
# Streamlit drops the state of widgets that are not drawn on a run (other role, other document type).
# Re-assigning each value keeps the form and limits intact when the user switches views.
for _k in [k for k in ss.keys() if k.startswith(("f_", "fin_", "sig_", "ov_", "limit_")) or k in ("sample_key", "live_gleif", "user_name")]:
    ss[_k] = ss[_k]

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("🛡️ Trade Authenticity Risk Scorer")
    st.caption("IS4234 Group 14 technology demonstrator. A course project, not a bank system and not affiliated "
               "with any bank named in it.")
    role = st.selectbox("Acting as", ROLES, key="role")
    if ss.get("_role_seen") != role:
        ss.user_name, ss._role_seen = DEFAULT_NAME[role], role
    st.text_input("Your name (recorded in the log)", key="user_name")
    st.divider()
    st.toggle("Live company lookup (GLEIF)", key="live_gleif",
              help="Looks the buyer up in the public GLEIF register. Falls back to example data if unreachable.")
    st.markdown("**Evidence labels**  \n🟢 Live source  \n🟡 Example data standing in for a bank-only or paid source  \n"
                "⚪ Entered by hand or not connected")
    st.divider()
    if st.button("Reset demonstrator"):
        for k in list(ss.keys()):
            del ss[k]
        st.rerun()
user = (ss.user_name or "").strip()

# ================================================================== OFFICER
if role == ROLES[0]:
    st.header("Trade finance officer")
    tab_new, tab_mine = st.tabs(["Submit a discounting request", "My requests"])
    with tab_new:
        st.subheader("1. Read the request documents")
        l, r_ = st.columns(2)
        with l:
            st.selectbox("Sample request pack", list(cases.PACKS), format_func=lambda k: cases.PACKS[k]["title"],
                         key="sample_key")
            st.button("Read sample pack", on_click=cb_load_sample, type="primary")
            pack = cases.PACKS[ss.sample_key]
            st.download_button("Download this sample PDF", (SAMPLES / pack["file"]).read_bytes(), pack["file"],
                               "application/pdf")
        with r_:
            st.file_uploader("Or upload a request pack (PDF)", type="pdf", key="upl", on_change=cb_upload)
            if ss.get("upload_error"):
                st.error(ss.pop("upload_error"))
            st.caption("Packs follow the sample layout: contract, invoice, transport document and request, one per page.")
        if ss.pack_name:
            st.success(f"Read {len(ss.docs)} documents from **{ss.pack_name}**: {', '.join(ss.docs)}. "
                       "The fields below were filled from the documents. Hin Leong packs are reconstructions "
                       "from the court record, not the original documents.")
        else:
            st.info("No pack read. You can also type a request in by hand below.")

        st.subheader("2. Request details")
        a, b, c = st.columns(3)
        a.text_input("Borrower (seller)", key="f_borrower")
        b.text_input("Buyer", key="f_buyer")
        c.number_input("Amount requested (US$)", min_value=0.0, step=100000.0, key="f_amount", format="%.2f")
        a.text_input("Product", key="f_product")
        b.number_input("Quantity (bbl)", min_value=0.0, step=1000.0, key="f_quantity")
        c.number_input("Unit price (US$/bbl)", min_value=0.0, step=0.5, key="f_unit_price")
        a.text_input("Invoice number", key="f_invoice_no")
        b.text_input("Invoice date (YYYY-MM-DD)", key="f_invoice_date")
        c.text_input("Payment due date (YYYY-MM-DD)", key="f_due_date")
        st.checkbox("Silent facility: the buyer is not told about the financing", key="f_silent")

        st.subheader("3. Transport document")
        a, b, c = st.columns(3)
        a.selectbox("Document type (decides which proof-of-movement rule runs)", cases.DOC_TYPES, key="f_doc_type")
        b.text_input("Issued by", key="f_issuer")
        c.text_input("Signed by", key="f_signatory")
        if ss.f_doc_type == "Bill of lading":
            a.text_input("Vessel", key="f_vessel")
            b.text_input("Port of loading", key="f_load_port")
            st.caption("Proof of movement for a bill of lading: ship tracking shows the vessel at the loading port.")
        elif ss.f_doc_type in ("Inter-tank transfer certificate", "Warehouse receipt"):
            a.text_input("Terminal or warehouse", key="f_terminal")
            st.caption("Proof of movement for storage: the operator confirms the transfer, and only counts if it is "
                       "independent of the borrower.")
        else:
            st.caption("No data source is connected for this document type in the demonstrator, so proof of movement "
                       "will read 'could not check' (50).")
        c.text_input("Document date (YYYY-MM-DD)", key="f_doc_date")

        with st.expander("4. Company accounts held by the bank (US$ millions)"):
            st.checkbox("The bank holds accounts for this borrower", key="f_has_accounts")
            if ss.records_note:
                st.caption(ss.records_note)
            cols = st.columns(4)
            for i, (k, label) in enumerate(FIN_FIELDS.items()):
                cols[i % 4].number_input(label, step=10.0, key="fin_" + k, disabled=not ss.f_has_accounts)
        with st.expander("5. Public signals: last 90 days against earlier 90-day periods"):
            st.caption(f"Needs at least {engine.MIN_HISTORY} earlier periods per signal, otherwise the signal reads "
                       "'could not check'. Example data standing in for news, ACRA and court feeds.")
            for k, label in engine.SIGNALS.items():
                a, b = st.columns([1, 3])
                a.number_input(f"{label}: last 90 days", min_value=0, step=1, key=f"sig_{k}_recent")
                b.text_input(f"{label}: earlier periods, comma separated", key=f"sig_{k}_hist")
        with st.expander("6. Example source responses (for trying other scenarios)"):
            st.caption("In production these answers come from the source. Leave on 'Use example table' to use the "
                       "demonstrator's example data, or set an answer by hand to see how the score reacts.")
            cols = st.columns(3)
            cols[0].selectbox("Trade Finance Registry", OV_OPTIONS, key="ov_registry")
            cols[1].selectbox("Proof the goods moved", OV_OPTIONS, key="ov_movement")
            cols[2].selectbox("Buyer exists", OV_OPTIONS, key="ov_buyer")
            cols[0].selectbox("Price near market", OV_OPTIONS, key="ov_price")
            cols[1].selectbox("Adverse news", OV_OPTIONS, key="ov_news")
            cols[2].selectbox("Transport document issuer", ["Use example table", "Independent", "Linked to borrower", "Unknown"],
                              key="ov_issuer")

        st.divider()
        if st.button("Run checks and submit", type="primary"):
            if not user:
                st.error("Enter your name in the sidebar. Every submission is logged against a name.")
            elif not ss.f_borrower.strip() or not ss.f_buyer.strip() or ss.f_amount <= 0:
                st.error("Borrower, buyer and amount are required.")
            else:
                req, overrides = collect_request()
                with st.spinner("Running the three checks"):
                    ev = cases.gather(req, overrides, gleif_lookup if ss.live_gleif else None)
                    rec = create_record(req, ev, user)
                ss.last_id = rec["id"]
                if rec["alert"]:
                    st.toast(f"{'Urgent alert' if rec['alert']['urgent'] else 'Alert'} sent to the Risk Management Unit "
                             f"for {rec['id']}", icon="🚨")
                else:
                    st.toast(f"{rec['id']} scored {rec['result']['band']}", icon="✅")
        last = next((x for x in ss.records if x["id"] == ss.last_id), None)
        if last:
            st.subheader(f"Result for {last['id']}")
            render_result(last, show_email=True)

    with tab_mine:
        st.dataframe(records_table(reversed(ss.records)), hide_index=True, width="stretch")
        waiting = [x for x in ss.records if x["status"] == "Awaiting officer note"]
        st.subheader("Medium requests waiting for a note")
        if not waiting:
            st.caption("None.")
        for x in waiting:
            with st.container(border=True):
                st.markdown(f"**{x['id']}** {x['req']['borrower']} to {x['req']['buyer']}, {money(x['req']['amount'])}, "
                            f"score {x['result']['overall']:.0f}")
                note = st.text_input("Short note explaining why this can be paid", key="note_" + x["id"])
                if st.button("Record note and pay", key="notebtn_" + x["id"]):
                    if not note.strip() or not user:
                        st.error("A note and your name are required.")
                    else:
                        add_note(x, user, note.strip())
                        st.rerun()
        st.subheader("Open a request")
        pick = st.selectbox("Request", [x["id"] for x in reversed(ss.records)], key="mine_pick")
        render_result(next(x for x in ss.records if x["id"] == pick), show_email=True)

# ================================================================== REVIEWER
elif role == ROLES[1]:
    st.header("Risk Management Unit reviewer")
    queue = [x for x in ss.records if x["status"] in PENDING]
    tab_q, tab_in, tab_done = st.tabs([f"Held and blocked ({len(queue)})", "Alert inbox", "Decided"])
    with tab_q:
        if not queue:
            st.success("Nothing is waiting. No payment is held or blocked.")
        else:
            st.dataframe(records_table(queue), hide_index=True, width="stretch")
            pick = st.selectbox("Open a request", [x["id"] for x in queue],
                                format_func=lambda i: next(f"{x['id']}  {x['result']['band']}  {x['req']['borrower']}  "
                                                           f"{money(x['req']['amount'])}" for x in queue if x["id"] == i))
            rec = next(x for x in queue if x["id"] == pick)
            render_result(rec, show_email=True)
            st.divider()
            st.subheader("Decision")
            st.caption("No money moves until a named reviewer decides. The reviewer cannot be the officer who "
                       "submitted the request.")
            action = st.radio("Action", ["Release", "Hold", "Reject"], horizontal=True, key="act_" + rec["id"],
                              captions=["Pay the request", "Keep on hold and ask for more information", "Refuse the request"])
            reason = st.text_area("Reason (recorded in the evidence log)", key="reason_" + rec["id"])
            if st.button("Record decision", type="primary", key="dec_" + rec["id"]):
                if not user:
                    st.error("Enter your name in the sidebar. Decisions must be made by a named reviewer.")
                elif user.lower() == rec["officer"].lower():
                    st.error("Segregation of duties: the submitting officer cannot review their own request.")
                elif not reason.strip():
                    st.error("A reason is required.")
                else:
                    decide(rec, action, user, reason.strip())
                    st.toast(f"{rec['id']}: {action.lower()} recorded", icon="📝")
                    st.rerun()
    with tab_in:
        alerts = [x for x in reversed(ss.records) if x["alert"]]
        st.caption("Alerts are shown here as on-screen notifications. In production they are sent by email to the "
                   "Risk Management Unit mailbox.")
        for x in alerts:
            a = x["alert"]
            with st.expander(f"{'🚨' if a['urgent'] else '⚠️'} {a['time']}  {a['subject']}  ·  now: {x['status']}"):
                st.code(f"To: {a['to']}\nSubject: {a['subject']}\n\n{a['body']}", language=None)
    with tab_done:
        done = [x for x in ss.records if x["decision"]]
        if done:
            st.dataframe(pd.DataFrame([dict(Request=x["id"], Borrower=x["req"]["borrower"], Amount=money(x["req"]["amount"]),
                                            Band=x["result"]["band"], Decision=x["decision"]["action"],
                                            By=x["decision"]["by"], At=x["decision"]["at"],
                                            Reason=x["decision"]["reason"]) for x in reversed(done)]),
                         hide_index=True, width="stretch")
        else:
            st.caption("No decisions yet.")

# ================================================================== BOARD
elif role == ROLES[2]:
    st.header("Board Risk Committee overview")
    st.caption("Exposure across all scored requests against the limits the board sets (R6). Requests other than "
               "those you submit in this session are synthetic.")
    recs = ss.records
    paid = [x for x in recs if x["status"] in PAID]
    silent_paid = sum(x["req"]["amount"] for x in paid if x["req"]["silent"]) / 1e6
    highrisk_paid = sum(x["req"]["amount"] for x in paid if x["result"]["band"] in ("High", "Critical")) / 1e6
    pending_amt = sum(x["req"]["amount"] for x in recs if x["status"] in PENDING) / 1e6
    rejected_amt = sum(x["req"]["amount"] for x in recs if x["status"] == "Rejected") / 1e6

    a, b = st.columns(2)
    a.number_input("Limit: silent exposure paid out (US$m)", min_value=0.0, step=10.0, key="limit_silent")
    b.number_input("Limit: High or Critical exposure released (US$m)", min_value=0.0, step=5.0, key="limit_highrisk")
    a, b, c, d = st.columns(4)
    a.metric("Silent exposure paid out", f"US${silent_paid:,.1f}m", f"{silent_paid / ss.limit_silent:.0%} of limit" if ss.limit_silent else None,
             delta_color="off")
    b.metric("High or Critical released", f"US${highrisk_paid:,.1f}m",
             f"{highrisk_paid / ss.limit_highrisk:.0%} of limit" if ss.limit_highrisk else None, delta_color="off")
    c.metric("Held or blocked before payout", f"US${pending_amt:,.1f}m")
    d.metric("Rejected", f"US${rejected_amt:,.1f}m")
    if silent_paid > ss.limit_silent:
        st.error(f"Limit breached: silent exposure of US${silent_paid:,.1f}m exceeds the US${ss.limit_silent:,.0f}m limit.")
    else:
        st.progress(min(silent_paid / ss.limit_silent, 1.0) if ss.limit_silent else 0.0, text="Silent exposure against limit")
    if highrisk_paid > ss.limit_highrisk:
        st.error(f"Limit breached: High or Critical exposure released of US${highrisk_paid:,.1f}m exceeds the "
                 f"US${ss.limit_highrisk:,.0f}m limit.")
    else:
        st.progress(min(highrisk_paid / ss.limit_highrisk, 1.0) if ss.limit_highrisk else 0.0,
                    text="High or Critical exposure released against limit")

    l, r_ = st.columns(2)
    with l:
        st.subheader("Requests by band")
        counts = pd.DataFrame({"Band": engine.BAND_ORDER,
                               "Requests": [sum(x["result"]["band"] == bnd for x in recs) for bnd in engine.BAND_ORDER],
                               "Amount (US$m)": [round(sum(x["req"]["amount"] for x in recs if x["result"]["band"] == bnd) / 1e6, 1)
                                                 for bnd in engine.BAND_ORDER]})
        st.bar_chart(counts, x="Band", y="Requests", sort=False)
        st.dataframe(counts, hide_index=True, width="stretch")
    with r_:
        st.subheader("Control indicators")
        reviewed = [x for x in recs if x["result"]["needs_review"]]
        decided = [x for x in reviewed if x["decision"] and x["decision"]["action"] in ("Release", "Reject")]
        released = [x for x in decided if x["decision"]["action"] == "Release"]
        paid_without = [x for x in reviewed if x["status"] in PAID and not x["decision"]]
        raised = sum(x["result"]["band"] != x["result"]["base_band"] for x in recs)
        st.metric("Registry checked before payout", "100%" if recs else "n/a")
        st.metric("Held or blocked requests paid without a named decision", len(paid_without))
        st.metric("Reviewer release rate on held and blocked requests",
                  f"{len(released) / len(decided):.0%}" if decided else "n/a",
                  help="A high rate would suggest the cut-offs are too tight.")
        st.metric("Requests where a rule, not the score, set the band", f"{raised} of {len(recs)}")
        st.metric("Silent deals recorded as exceptions (R4)", sum(x["req"]["silent"] for x in recs))
    st.subheader("All requests")
    st.dataframe(records_table(reversed(recs)), hide_index=True, width="stretch")

# ================================================================== AUDIT
else:
    st.header("Compliance and Internal Audit")
    tab_log, tab_map, tab_how = st.tabs(["Evidence log", "Checks mapped to expectations", "How the score works"])
    with tab_log:
        st.caption("Every score, rule, alert, note and decision, with a name and a time. Kept in one place rather "
                   "than in emails or separate systems.")
        df = pd.DataFrame(list(reversed(ss.log)))
        f1, f2 = st.columns(2)
        pick = f1.selectbox("Request", ["All"] + [x["id"] for x in reversed(ss.records)])
        ev_pick = f2.selectbox("Event", ["All"] + sorted(df["Event"].unique()))
        if pick != "All":
            df = df[df["Request"] == pick]
        if ev_pick != "All":
            df = df[df["Event"] == ev_pick]
        st.dataframe(df, hide_index=True, width="stretch", height=480)
        st.download_button("Download log (CSV)", pd.DataFrame(ss.log).to_csv(index=False), "evidence_log.csv", "text/csv")
    with tab_map:
        st.caption("Each check is linked to the supervisory expectation it helps meet and to its owner (R4).")
        st.dataframe(pd.DataFrame([
            ("Check 1: issuer independence", "Confirm that related-party deals are genuine", "MAS (2015) paras 2.29 to 2.30", "Transaction management unit", "W2, RC5"),
            ("Check 1: consistency and file metadata", "Review trade documents for anomalies", "MAS (2015) para 2.11", "Transaction management unit", "W1"),
            ("Check 2: company health and behaviour", "Look beyond the customer's credit quality; structured customer fraud assessment", "MAS (2015) para 2.17; MAS (2025) para 2.27", "Risk Management Unit", "RC1"),
            ("Check 3: registry duplicate check", "Check and log duplicate financing before payout", "ABS Code (2020); ABS Trade Finance Registry (2023)", "Middle office", "W2"),
            ("Check 3: proof the goods moved", "Verify deals against independent sources, including vessel checks", "MAS (2015) paras 2.11, 2.13; MAS (2025)", "Transaction management unit", "W1, RC5"),
            ("Check 3: buyer exists and is independent", "Verify against independent sources", "MAS (2015) para 2.11", "Transaction management unit", "W1"),
            ("Check 3: price near market", "Compare prices with outside data", "MAS (2015)", "Financial crime compliance", "RC4"),
            ("Linked-issuer rule: at least High", "Documents from linked companies do not count as proof", "Report recommendation R2", "Risk Management Unit", "W2"),
            ("Named reviewer on High and Critical", "Independent risk function challenges risky decisions", "BCBS (2015) paras 38 to 43", "Risk Management Unit", "RC1, RC3"),
            ("Large silent deals held, never same-day", "Investigate departures from policy; no same-day payout", "MAS (2013) s. 2.1.3; recommendation R3", "Risk Management Unit", "W3, RC3"),
            ("Silent deal recorded as an exception", "Obtain buyer acknowledgement of assignment, or record the exception", "ABS Code (2020), as cited in MAS (2025)", "Compliance", "W3, RC2"),
            ("Single evidence log", "Consolidated exception tracking", "MAS (2025) para 2.55", "Compliance", "RC3"),
            ("Board exposure limits", "Risk appetite becomes limits for each business line", "FSB (2013); recommendation R6", "Board Risk Committee", "RC1"),
            ("Fraud considered in risk assessment", "Assess fraud risk explicitly", "COSO (2013) Principle 8", "Head of GTRF; Chief Risk Officer", "W1, RC1"),
        ], columns=["Control in the scorer", "Expectation", "Source", "Owner", "Weakness or root cause addressed"]),
            hide_index=True, width="stretch", height=530)
        st.caption("Sources are as cited in the group's report. MAS papers are supervisory expectations, not binding rules.")
    with tab_how:
        st.markdown(f"""
**Overall risk score** = 0.3 × Check 1 + 0.3 × Check 2 + 0.4 × Check 3

| Band | Score | What happens to the payment | Who is told |
|---|---|---|---|
| Low | 0 to 29 | Paid as normal | Logged only |
| Medium | 30 to 49 | Paid once the officer records a short note | Logged only |
| High | 50 to 69 | Held until a named reviewer approves | Alert to the Risk Management Unit |
| Critical | 70 to 100, or any contradiction | Blocked until the reviewer clears it | Urgent alert to the Risk Management Unit |

**Check 1: Are the documents sound?** Three tests. Issuer linked to the borrower adds 40. Documents disagreeing adds 30.
Signs of editing in the file metadata adds 30. Capped at 100.

**Check 2: Is the company healthy and behaving normally?** Check 2 = 0.5 × (100 − Health) + 0.5 × Deviation.
- Health uses Altman Z'' = 6.56X1 + 3.26X2 + 6.72X3 + 1.05X4. Health is 0 at Z'' of {engine.Z_DISTRESS} or below, 100 at
  {engine.Z_SAFE} or above, and a straight line between. No accounts scores 50.
- Deviation takes five public signals. For each, z = (last 90 days − the company's own average) ÷ its own standard
  deviation (minimum 1). The signal score is z ÷ 3 × 100, limited to 0 to 100. Deviation is the average of the five.
  Fewer than {engine.MIN_HISTORY} earlier periods scores 50.

**Check 3: Does the outside world agree the sale happened?** Five questions. Agrees = 0, could not check = 50,
disagrees = 100. Check 3 is the average. The proof-of-movement rule depends on the transport document type:
ship tracking for a bill of lading, operator confirmation for storage, and "could not check" where no source is connected.
Price is "near market" within ±{engine.PRICE_BAND:.0%} of the benchmark.

**Rules that override the score**
1. Any outside evidence that contradicts the deal sends the request straight to Critical. Adverse news raises the score
   but is not treated as a contradiction.
2. A transport document issued or signed by a party linked to the borrower makes the request at least High.
3. A silent deal of US${engine.R3_THRESHOLD / 1e6:.0f}m or more is held for a named reviewer whatever its score.

**Limits.** The weights and cut-offs are starting values and have not been calibrated. The score is not a probability.
Sources marked 🟡 return example data in this demonstrator.
""")
