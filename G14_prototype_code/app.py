"""Trade Authenticity Risk Scorer: Streamlit demonstrator (IS4234 Group 14)."""
import html
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

st.set_page_config(page_title="Trade Authenticity Risk Scorer", layout="wide")
ss = st.session_state


# ------------------------------------------------------------------ helpers
def now():
    return datetime.now(SGT)


def ts(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


def money(x):
    return f"US${x:,.0f}" if x < 1e6 else f"US${x / 1e6:,.2f}m"


OUTCOME = {
    "Blocked": "Payment blocked", "Held": "Payment held", "Held (large silent deal)": "Payment held",
    "Awaiting officer note": "Payment pending officer note", "Paid": "Payment released",
    "Paid (note recorded)": "Payment released after officer note", "Released by reviewer": "Payment released by reviewer",
    "On hold: more information requested": "Payment on hold: more information requested", "Rejected": "Request rejected",
}
PILL = {"pass": "#2e7d32", "confirmed": "#2e7d32", "fail": "#c62828", "contradicted": "#c62828",
        "adverse": "#b45309", "unchecked": "#6b7280"}
CSS = """<style>
.tar-card{border:1px solid rgba(128,128,128,.35);border-left:6px solid var(--c,#888);border-radius:6px;
  padding:16px 20px;margin:4px 0 16px}
.tar-label{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;opacity:.7;font-weight:600;margin:0 0 4px}
.tar-outcome{font-size:1.4rem;font-weight:700;margin:0 0 4px;line-height:1.25}
.tar-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-top:14px;
  border-top:1px solid rgba(128,128,128,.25);padding-top:12px}
.tar-grid .v{font-size:1.02rem;font-weight:600;line-height:1.35}
.tar-kv{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 24px}
table.tar{width:100%;border-collapse:collapse;font-size:.86rem;margin:2px 0 16px}
table.tar th{text-align:left;font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;opacity:.7;
  border-bottom:2px solid rgba(128,128,128,.45);padding:6px 10px;vertical-align:bottom;font-weight:600}
table.tar td{border-bottom:1px solid rgba(128,128,128,.22);padding:9px 10px;vertical-align:top;line-height:1.45}
table.tar td.num{text-align:right;white-space:nowrap}
.tar-pill{display:inline-block;padding:2px 9px;border-radius:3px;font-size:.7rem;font-weight:700;letter-spacing:.05em;
  text-transform:uppercase;white-space:nowrap;color:#fff}
.tar-src{display:inline-block;padding:1px 7px;border:1px solid currentColor;border-radius:3px;font-size:.66rem;
  font-weight:600;letter-spacing:.05em;text-transform:uppercase;white-space:nowrap;opacity:.8;margin-bottom:3px}
.tar-note{font-size:.84rem;opacity:.75}
</style>"""


def esc(x):
    return html.escape(str(x if x is not None else ""))


def pill(result, text=None):
    return f"<span class='tar-pill' style='background:{PILL[result]}'>{esc(text or engine.RESULT_LABEL[result])}</span>"


def band_pill(band):
    return f"<span class='tar-pill' style='background:{BAND_COLOUR[band]};font-size:.8rem;padding:3px 12px'>{band}</span>"


def src(source):
    """Source label: whether the evidence is live, example data, or entered by hand."""
    kind, _, rest = source.partition(":")
    if kind.startswith("Live"):
        tag, rest = "Live source", rest.strip()
    elif kind.startswith("Example"):
        tag, rest = "Example data", rest.strip()
    elif source == "Entered by hand":
        tag, rest = "Manual entry", ""
    elif source == "Not connected":
        tag, rest = "Not connected", ""
    else:
        tag, rest = "Bank record", source
    rest = rest[:1].upper() + rest[1:]
    return f"<span class='tar-src'>{tag}</span>" + (f"<br>{esc(rest)}" if rest else "")


def html_table(headers, rows):
    head = "".join(f"<th style='width:{w}'>{esc(h)}</th>" if w else f"<th>{esc(h)}</th>" for h, w in headers)
    body = "".join("<tr>" + "".join(c if c.startswith("<td") else f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    st.markdown(f"<table class='tar'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>", unsafe_allow_html=True)


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
    L = [
        f"Request {rec['id']}: {OUTCOME[r['status']].lower()} before payout.",
        "",
        "REQUEST",
        f"  Borrower:            {q['borrower']}",
        f"  Buyer:               {q['buyer']}",
        f"  Amount:              {money(q['amount'])}",
        f"  Facility:            {'Silent (buyer not notified)' if q['silent'] else 'Disclosed'}",
        f"  Invoice:             {q['invoice_no'] or 'not stated'}",
        f"  Transport document:  {q['doc_type']}",
        f"  Submitted by:        {rec['officer']} at {rec['submitted']}",
        "",
        "DECISION",
        f"  Band:                {r['band']}",
        f"  Basis:               {r['basis']}",
        f"  Weighted score:      {r['overall']:.0f} of 100 ({r['base_band']} on the score alone)",
        f"  Check 1 Documents:   {r['c1']['score']:.0f}",
        f"  Check 2 Company:     {r['c2']['score']:.0f}",
        f"  Check 3 Transaction: {r['c3']['score']:.0f}",
        "",
        "FINDINGS",
    ]
    if not r["findings"]:
        L.append("  No adverse findings.")
    for f in r["findings"][:3]:
        L += [f"  {f['id']}. {f['check']}, test {f['test_id']}: {f['test']}",
              f"      Document: {f['doc_ref']}",
              f"      Stated:   {f['stated']}",
              f"      Found:    {f['found']}",
              f"      Source:   {f['source']}",
              f"      Effect:   {f['effect']}"]
    L += ["", "NOT VERIFIED"]
    L += [f"  - {u['check']}, test {u['test_id']}: {u['reason']}" for u in r["unverified"]] or ["  Nothing. Every test returned a result."]
    L += ["", "HOW THE BAND WAS DETERMINED"] + [f"  {i}. {x}" for i, x in enumerate(r["steps"], start=1)]
    L += ["", "RECOMMENDED ACTION",
          "  " + ("Do not release. Verify the transaction through an independent party before any payout."
                  if urgent else "Review the findings and record a decision to release, hold or reject.")]
    return dict(to="rmu-alerts@demo-bank.example", time=rec["submitted"], urgent=urgent,
                subject=f"{'URGENT: ' if urgent else ''}{r['band']} risk, {rec['id']}, {q['borrower']}, {money(q['amount'])}",
                body="\n".join(L))


def create_record(req, ev, officer, when=None):
    when = when or now()
    res = engine.score_request(req, ev)
    rid = f"REQ-{len(ss.records) + 1:04d}"
    rec = dict(id=rid, req=req, ev=ev, result=res, status=res["status"], officer=officer, submitted=ts(when),
               note="", decision=None, alert=None)
    ss.records.append(rec)
    log("Request scored", rid, "Scorer (automatic)",
        f"Weighted score {res['overall']:.0f} ({res['base_band']} on the score alone). Final band {res['band']}. "
        f"Basis: {res['basis']}. Checks: {res['c1']['score']:.0f} / {res['c2']['score']:.0f} / {res['c3']['score']:.0f}. "
        f"Status: {res['status']}.", when)
    for f in res["findings"]:
        log("Finding recorded", rid, "Scorer (automatic)",
            f"{f['id']} ({f['check']}, test {f['test_id']}). Document: {f['doc_ref']}. Stated: {f['stated']} "
            f"Found: {f['found']} Source: {f['source']}. Effect: {f['effect']}", when)
    if req.get("silent"):
        log("Exception recorded", rid, "Scorer (automatic)",
            "Silent deal: buyer acknowledgement of assignment not obtained (ABS Code). Recorded as an exception (R4).", when)
    if res["needs_review"]:
        rec["alert"] = alert_email(rec)
        log("Alert sent", rid, "Scorer (automatic)", f"To {rec['alert']['to']}: {rec['alert']['subject']}", when)
    elif res["status"] == "Paid":
        log("Payment released", rid, "Scorer (automatic)", "Low band: paid as normal.", when)
    return rec


def decide(rec, action, reviewer, reason, cited=None, when=None):
    new = {"Release": "Released by reviewer", "Hold": "On hold: more information requested", "Reject": "Rejected"}[action]
    rec["status"] = new
    rec["decision"] = dict(action=action, by=reviewer, reason=reason, cited=list(cited or []), at=ts(when or now()))
    log(f"Decision: {action.lower()}", rec["id"], reviewer,
        reason + (f" Findings relied on: {', '.join(cited)}." if cited else ""), when)
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
                decide(rec, dec[0], dec[1], dec[2], [f["id"] for f in rec["result"]["findings"][:2]],
                       when + timedelta(hours=3))


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
    ss.docs, ss.forensics, ss.pack_name, ss.records_note, ss.pages = {}, None, "", "", {}


def load_pack(data, name):
    fields, docs, forensics = cases.read_pack(data)
    reset_form()
    for k in TEXT_FIELDS:
        ss["f_" + k] = fields.get(k, "") or ""
    for k in NUM_FIELDS:
        ss["f_" + k] = float(fields.get(k) or 0)
    ss.f_silent = bool(fields.get("silent"))
    ss.f_doc_type = fields["doc_type"] if fields.get("doc_type") in cases.DOC_TYPES else cases.DOC_TYPES[0]
    ss.docs, ss.forensics, ss.pack_name, ss.pages = docs, forensics, name, fields.get("pages", {})
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
    fields["pages"] = ss.pages
    req = cases.build_request(fields, ss.docs, ss.forensics, ss.pack_name)
    req["records_note"] = ss.records_note
    overrides = {k: ss["ov_" + k] for k in ("registry", "movement", "buyer", "price", "news")}
    overrides["issuer"] = ss.ov_issuer
    return req, overrides


# ------------------------------------------------------------------ result view
def section(title, caption=None):
    st.markdown(f"<div class='tar-label' style='margin-top:18px'>{esc(title)}</div>", unsafe_allow_html=True)
    if caption:
        st.markdown(f"<div class='tar-note' style='margin-bottom:6px'>{esc(caption)}</div>", unsafe_allow_html=True)


def test_rows(items, c3=False):
    rows = []
    for t in items:
        result = t["status"] if c3 else t["result"]
        if c3 and result == "contradicted" and not t.get("hard", True):
            result = "adverse"
        rows.append([f"<td class='num'>{esc(t['id'])}</td>", f"<b>{esc(t['name'])}</b>", pill(result),
                     esc(t["doc_ref"]), esc(t["stated"]), esc(t["detail"] if c3 else t["found"]), src(t["source"]),
                     f"<td class='num'>{t['points']:.0f}</td>"])
    return rows


TEST_HEADERS = [("Test", "4%"), ("What is tested", "15%"), ("Result", "9%"), ("Document examined", "19%"),
                ("Stated in the document", "19%"), ("Evidence found", "20%"), ("Source", "9%"), ("Points", "5%")]


def render_result(rec, show_email=False):
    r, q = rec["result"], rec["req"]
    st.markdown(CSS, unsafe_allow_html=True)
    dcn = rec["decision"]

    # ---- 1. Decision
    if dcn:
        sub = f"{dcn['action']} recorded by {dcn['by']} at {dcn['at']}."
    elif rec["note"]:
        sub = "Released after the submitting officer recorded a note."
    else:
        sub = r["action"]
    st.markdown(
        f"<div class='tar-card' style='--c:{BAND_COLOUR[r['band']]}'>"
        f"<div class='tar-label'>Decision record · {esc(rec['id'])}</div>"
        f"<div class='tar-outcome'>{esc(OUTCOME[rec['status']])}</div><div>{esc(sub)}</div>"
        f"<div class='tar-grid'>"
        f"<div><div class='tar-label'>Risk band</div><div class='v'>{band_pill(r['band'])}</div></div>"
        f"<div><div class='tar-label'>Weighted score</div><div class='v'>{r['overall']:.0f} of 100</div></div>"
        f"<div><div class='tar-label'>Band on the score alone</div><div class='v'>{r['base_band']} ({engine.BAND_RANGE[r['base_band']]})</div></div>"
        f"<div><div class='tar-label'>Basis of the band</div><div class='v'>{esc(r['basis'])}</div></div>"
        f"</div></div>", unsafe_allow_html=True)

    # ---- 2. Why, when the band or the hold is not explained by the score
    if r["explanation"]:
        title = "Why the band is higher than the score indicates" if r["band"] != r["base_band"] else "Why payment is held"
        rows = "".join(f"<tr><td style='width:20%;font-weight:600'>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in r["explanation"])
        st.markdown(f"<div class='tar-card' style='--c:{BAND_COLOUR[r['band']]}'><div class='tar-label'>{title}</div>"
                    f"<table class='tar' style='margin:6px 0 0'><tbody>{rows}</tbody></table></div>", unsafe_allow_html=True)

    # ---- 3. Findings
    section("Findings supporting the decision",
            "Each finding names the check and test, the document and field examined, what the document states, "
            "and the evidence that was found.")
    if r["findings"]:
        html_table([("Ref", "4%"), ("Check and test", "15%"), ("Document examined", "18%"), ("Stated in the document", "18%"),
                    ("Evidence found", "19%"), ("Source", "9%"), ("Effect on the decision", "17%")],
                   [[f"<b>{f['id']}</b>",
                     f"{esc(f['check'])}<br>Test {esc(f['test_id'])}: {esc(f['test'])}<br>{pill(f['result'])}",
                     esc(f["doc_ref"]), esc(f["stated"]), esc(f["found"]), src(f["source"]), esc(f["effect"])]
                    for f in r["findings"]])
    else:
        st.markdown("No adverse findings. Every test either passed or is listed below as not verified.")

    section("Items that could not be verified",
            "An unverified item is never scored as safe. It is reported here so the gap is visible to the reviewer.")
    if r["unverified"]:
        html_table([("Check and test", "20%"), ("Document examined", "24%"), ("Why it could not be verified", "28%"),
                    ("Source", "10%"), ("Effect on the score", "18%")],
                   [[f"{esc(u['check'])}<br>Test {esc(u['test_id'])}: {esc(u['test'])}", esc(u["doc_ref"]), esc(u["reason"]),
                     src(u["source"]), esc(u["effect"])] for u in r["unverified"]])
    else:
        st.markdown("None. Every test returned a result.")

    section("How the band was determined")
    st.markdown("\n".join(f"{i}. {x}" for i, x in enumerate(r["steps"], start=1)))

    # ---- 4. Request
    section("Request")
    kv = [("Borrower", q["borrower"]), ("Buyer", q["buyer"]), ("Amount requested", money(q["amount"])),
          ("Facility", "Silent: buyer not notified" if q["silent"] else "Disclosed to the buyer"),
          ("Invoice", q["invoice_no"] or "Not stated"), ("Transport document", q["doc_type"]),
          ("Document pack", q.get("pack_name") or "None: entered by hand"),
          ("Submitted by", rec["officer"]), ("Submitted at", rec["submitted"])]
    st.markdown("<div class='tar-card'><div class='tar-kv'>" + "".join(
        f"<div><div class='tar-label'>{esc(k)}</div><div>{esc(v)}</div></div>" for k, v in kv) + "</div></div>",
        unsafe_allow_html=True)

    # ---- 5. Detail by check
    with st.expander(f"Check 1: Documents. Score {r['c1']['score']:.0f} of 100, weight 30%", expanded=True):
        st.markdown("<div class='tar-note'>Are the documents sound? A linked issuer adds 40 points and each other "
                    "failure adds 30, to a maximum of 100.</div>", unsafe_allow_html=True)
        html_table(TEST_HEADERS, test_rows(r["c1"]["tests"]))
    with st.expander(f"Check 2: Company. Score {r['c2']['score']:.0f} of 100, weight 30%", expanded=True):
        st.markdown("<div class='tar-note'>Is the company healthy and behaving normally for itself? "
                    "Check 2 = 0.5 × (100 − financial health) + 0.5 × deviation.</div>", unsafe_allow_html=True)
        z = r["c2"]["z"]
        html_table([("Test", "4%"), ("What is tested", "18%"), ("Result", "30%"), ("Basis", "33%"), ("Source", "15%")], [
            ["<td class='num'>2.1</td>", "<b>Financial health</b>", esc(r["c2"]["health_detail"]),
             esc((f"X1 {z['x1']:.3f}, X2 {z['x2']:.3f}, X3 {z['x3']:.3f}, X4 {z['x4']:.3f}. " if z else "")
                 + (q.get("records_note") or "")),
             src("Example data: accounts held by the bank")],
            ["<td class='num'>2.2</td>", "<b>Deviation from its own normal</b>",
             f"{r['c2']['deviation']:.0f} out of 100, the average of the five signals below.",
             "Last 90 days compared with the company's own earlier 90-day periods over five years.",
             src("Example data: news, ACRA and court records")]])
        html_table([("Signal", "24%"), ("Last 90 days", "14%"), ("Normal for this company", "20%"), ("Signal score", "14%"),
                    ("Reading", "28%")],
                   [[esc(x["signal"]), f"<td class='num'>{esc(x['recent'])}</td>",
                     f"<td class='num'>{esc(x['normal'] if x['normal'] is not None else 'Not available')}</td>",
                     f"<td class='num'>{x['score']:.0f}</td>", esc(x["reading"])] for x in r["c2"]["signals"]])
        st.markdown(f"Check 2 = 0.5 × (100 − {r['c2']['health']:.0f}) + 0.5 × {r['c2']['deviation']:.0f} = "
                    f"**{r['c2']['score']:.0f}**")
    with st.expander(f"Check 3: Transaction. Score {r['c3']['score']:.0f} of 100, weight 40%", expanded=True):
        st.markdown("<div class='tar-note'>Does independent evidence agree that the sale happened? Verified scores 0, "
                    "not verified scores 50, contradicted scores 100. Check 3 is the average of the five tests.</div>",
                    unsafe_allow_html=True)
        html_table(TEST_HEADERS, test_rows(r["c3"]["subs"], c3=True))
    st.markdown(f"<div class='tar-note'>Weighted score = 0.3 × {r['c1']['score']:.0f} + 0.3 × {r['c2']['score']:.0f} + "
                f"0.4 × {r['c3']['score']:.0f} = {r['overall']:.1f}. This is a risk score, not a probability of fraud. "
                "It supports the reviewer's judgement and does not prove fraud.</div>", unsafe_allow_html=True)

    # ---- 6. Alert, note and decision
    if show_email and rec["alert"]:
        with st.expander("Alert sent to the Risk Management Unit"):
            st.code(f"To:      {rec['alert']['to']}\nSent:    {rec['alert']['time']}\nSubject: {rec['alert']['subject']}\n\n"
                    + rec["alert"]["body"], language=None)
    if rec["note"]:
        st.markdown(f"<div class='tar-card' style='--c:#2e7d32'><div class='tar-label'>Officer note</div>"
                    f"{esc(rec['note'])}</div>", unsafe_allow_html=True)
    if dcn:
        colour = {"Release": "#2e7d32", "Hold": "#b45309", "Reject": "#c62828"}[dcn["action"]]
        st.markdown(
            f"<div class='tar-card' style='--c:{colour}'><div class='tar-label'>Reviewer decision</div>"
            f"<div class='tar-outcome' style='font-size:1.1rem'>{esc(dcn['action'])} by {esc(dcn['by'])}, {esc(dcn['at'])}</div>"
            f"<div><b>Findings relied on:</b> {esc(', '.join(dcn.get('cited') or []) or 'None cited')}</div>"
            f"<div><b>Reason:</b> {esc(dcn['reason'])}</div></div>", unsafe_allow_html=True)


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
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("Trade Authenticity Risk Scorer")
    st.caption("IS4234 Group 14 technology demonstrator. A course project, not a bank system and not affiliated "
               "with any bank named in it.")
    role = st.selectbox("Acting as", ROLES, key="role")
    if ss.get("_role_seen") != role:
        ss.user_name, ss._role_seen = DEFAULT_NAME[role], role
    st.text_input("Your name (recorded in the log)", key="user_name")
    st.divider()
    st.toggle("Live company lookup (GLEIF)", key="live_gleif",
              help="Looks the buyer up in the public GLEIF register. Falls back to example data if unreachable.")
    st.markdown("<div class='tar-label'>Evidence labels</div>"
                "<div class='tar-note'><span class='tar-src'>Live source</span> Read or looked up in real time<br>"
                "<span class='tar-src'>Example data</span> Stands in for a bank-only or paid source<br>"
                "<span class='tar-src'>Manual entry</span> Set by hand on the request form</div>", unsafe_allow_html=True)
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
                             f"for {rec['id']}")
                else:
                    st.toast(f"{rec['id']} scored {rec['result']['band']}")
        last = next((x for x in ss.records if x["id"] == ss.last_id), None)
        if last:
            st.subheader(f"Result for {last['id']}")
            st.caption("The full decision record is below. It is also sent to the reviewer and written to the evidence log.")
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
            opts = {f["id"]: f"{f['id']}: {f['check']}, test {f['test_id']}, {f['test']}" for f in rec["result"]["findings"]}
            cited = st.multiselect("Findings relied on", list(opts), format_func=lambda k: opts[k], key="cite_" + rec["id"],
                                   help="Select the findings that support this decision. They are recorded with it.")
            reason = st.text_area("Reason (recorded in the evidence log)", key="reason_" + rec["id"],
                                  placeholder="State why, with reference to the findings and any further verification carried out.")
            if st.button("Record decision", type="primary", key="dec_" + rec["id"]):
                if not user:
                    st.error("Enter your name in the sidebar. Decisions must be made by a named reviewer.")
                elif user.lower() == rec["officer"].lower():
                    st.error("Segregation of duties: the submitting officer cannot review their own request.")
                elif not reason.strip():
                    st.error("A reason is required.")
                elif action == "Reject" and opts and not cited:
                    st.error("Select at least one finding that the rejection relies on.")
                else:
                    decide(rec, action, user, reason.strip(), cited)
                    st.toast(f"{rec['id']}: {action.lower()} recorded")
                    st.rerun()
    with tab_in:
        alerts = [x for x in reversed(ss.records) if x["alert"]]
        st.caption("Alerts are shown here as on-screen notifications. In production they are sent by email to the "
                   "Risk Management Unit mailbox.")
        for x in alerts:
            a = x["alert"]
            with st.expander(f"{a['time']}  |  {a['subject']}  |  Current status: {x['status']}"):
                st.code(f"To:      {a['to']}\nSubject: {a['subject']}\n\n{a['body']}", language=None)
    with tab_done:
        done = [x for x in ss.records if x["decision"]]
        if done:
            st.dataframe(pd.DataFrame([dict(Request=x["id"], Borrower=x["req"]["borrower"], Amount=money(x["req"]["amount"]),
                                            Band=x["result"]["band"], Decision=x["decision"]["action"],
                                            By=x["decision"]["by"], At=x["decision"]["at"],
                                            Findings=", ".join(x["decision"].get("cited") or []) or "None cited",
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
Sources labelled Example data return example data in this demonstrator.
""")
