"""Sample document packs, example source tables, the document reader and evidence gathering.

Everything in the EXAMPLE tables stands in for a bank-only or paid data source and is
labelled "Example data" on screen. Nothing here is company-specific logic: the tables
are data, and gather() applies the same rules to any request.
"""
import io
import re
from datetime import date, datetime

from pypdf import PdfReader

import engine

EX = "Example data"
STAMP = "RECONSTRUCTION FOR DEMONSTRATION - NOT THE ORIGINAL DOCUMENT"
STAMP_FICT = "SYNTHETIC DOCUMENT FOR DEMONSTRATION - FICTIONAL COMPANIES"

HLT = "Hin Leong Trading (Pte) Ltd"
CAO = "China Aviation Oil (Singapore) Corporation Ltd"
UNIPEC = "Unipec Singapore Pte Ltd"
OTK = "Ocean Tankers (Pte) Ltd"
UT = "Universal Terminal (S) Pte Ltd"
SME = "Straits Meridian Energy Pte Ltd"
NORD = "Nordhaven Refining BV"
PCS = "Pacific Crest Shipping Pte Ltd"

DOC_TYPES = [
    "Bill of lading",
    "Inter-tank transfer certificate",
    "Warehouse receipt",
    "Air waybill",
    "Truck or rail consignment note",
]

# ------------------------------------------------------------------ document packs
# Each pack becomes a 4-page PDF in sample_docs/. Values marked (invented) in the
# guide are not in the court record.
PACKS = {
    "cao": dict(
        title="Hin Leong to CAO, 19 Mar 2020 (replay)", file="HLT_CAO_request_pack.pdf", stamp=STAMP,
        pages=[
            ("Sales Contract", [("Seller", HLT), ("Buyer", CAO), ("Product", "Gasoil 10ppm sulphur"),
                                ("Quantity (bbl)", "1,300,000"), ("Unit price (USD/bbl)", "43.25"),
                                ("Contract value (USD)", "56,225,000.00"), ("Contract date", "2020-02-26"),
                                ("Payment terms (days)", "30")]),
            ("Commercial Invoice", [("Invoice no", "SO-102780"), ("Seller", HLT), ("Buyer", CAO),
                                    ("Product", "Gasoil 10ppm sulphur"), ("Quantity (bbl)", "1,300,000"),
                                    ("Invoice amount (USD)", "56,225,000.00"), ("Invoice date", "2020-03-18"),
                                    ("Payment due date", "2020-04-17")]),
            ("Inter-Tank Transfer Certificate", [("Document type", "Inter-tank transfer certificate"),
                                                 ("Issuer", UT), ("Signed by", "Terminal operations (signature illegible)"),
                                                 ("Shipper", HLT), ("Consignee", CAO),
                                                 ("Product", "Gasoil 10ppm sulphur"), ("Quantity (bbl)", "1,300,000"),
                                                 ("Vessel", ""), ("Port of loading", ""), ("Terminal", UT),
                                                 ("Document date", "2020-03-17")]),
            ("Discounting Request", [("Borrower", HLT), ("Facility", "Silent confirmation and discounting (buyer not notified)"),
                                     ("Amount requested (USD)", "56,065,852.74"), ("Request date", "2020-03-19")]),
        ]),
    "unipec": dict(
        title="Hin Leong to Unipec, 20 Mar 2020 (replay)", file="HLT_Unipec_request_pack.pdf", stamp=STAMP,
        pages=[
            ("Sales Contract", [("Seller", HLT), ("Buyer", UNIPEC), ("Product", "Gasoil 10ppm sulphur"),
                                ("Quantity (bbl)", "1,290,000"), ("Unit price (USD/bbl)", "43.40"),
                                ("Contract value (USD)", "55,986,000.00"), ("Contract date", "2020-03-19"),
                                ("Payment terms (days)", "45")]),
            ("Commercial Invoice", [("Invoice no", "SO-102795"), ("Seller", HLT), ("Buyer", UNIPEC),
                                    ("Product", "Gasoil 10ppm sulphur"), ("Quantity (bbl)", "1,290,000"),
                                    ("Invoice amount (USD)", "55,986,000.00"), ("Invoice date", "2020-03-20"),
                                    ("Payment due date", "2020-05-04")]),
            ("Bill of Lading", [("Document type", "Bill of lading"), ("Issuer", OTK),
                                ("Signed by", "Manager, " + HLT), ("Shipper", HLT), ("Consignee", UNIPEC),
                                ("Product", "Gasoil 10ppm sulphur"), ("Quantity (bbl)", "1,290,000"),
                                ("Vessel", "MT Ocean Demo (placeholder name)"), ("Port of loading", "Singapore"),
                                ("Terminal", ""), ("Document date", "2020-03-18")]),
            ("Discounting Request", [("Borrower", HLT), ("Facility", "Silent confirmation and discounting (buyer not notified)"),
                                     ("Amount requested (USD)", "55,803,699.87"), ("Request date", "2020-03-20")]),
        ]),
    "genuine": dict(
        title="Straits Meridian to Nordhaven (genuine, fictional)", file="Genuine_request_pack.pdf", stamp=STAMP_FICT,
        pages=[
            ("Sales Contract", [("Seller", SME), ("Buyer", NORD), ("Product", "Gasoil 10ppm sulphur"),
                                ("Quantity (bbl)", "100,000"), ("Unit price (USD/bbl)", "42.10"),
                                ("Contract value (USD)", "4,210,000.00"), ("Contract date", "2020-03-02"),
                                ("Payment terms (days)", "30")]),
            ("Commercial Invoice", [("Invoice no", "SME-INV-20417"), ("Seller", SME), ("Buyer", NORD),
                                    ("Product", "Gasoil 10ppm sulphur"), ("Quantity (bbl)", "100,000"),
                                    ("Invoice amount (USD)", "4,210,000.00"), ("Invoice date", "2020-03-12"),
                                    ("Payment due date", "2020-04-11")]),
            ("Bill of Lading", [("Document type", "Bill of lading"), ("Issuer", PCS),
                                ("Signed by", "Master, MT Pacific Heron, for " + PCS), ("Shipper", SME),
                                ("Consignee", NORD), ("Product", "Gasoil 10ppm sulphur"),
                                ("Quantity (bbl)", "100,000"), ("Vessel", "MT Pacific Heron"),
                                ("Port of loading", "Singapore"), ("Terminal", ""), ("Document date", "2020-03-11")]),
            ("Discounting Request", [("Borrower", SME), ("Facility", "Silent confirmation and discounting (buyer not notified)"),
                                     ("Amount requested (USD)", "4,197,400.00"), ("Request date", "2020-03-12")]),
        ]),
}


def _hist(base, wobble):
    """20 past 90-day periods (five years) around a base level."""
    return [max(0, base + w) for w in (wobble * 5)[:20]]


# ------------------------------------------------------------------ bank-held records (example)
# Accounts the bank holds as a condition of credit, in USD millions, and counts of
# five public signals: last 90 days and the 20 previous 90-day periods.
BORROWER_RECORDS = {
    HLT.lower(): dict(
        note="Illustrative figures consistent with the accounts lenders held (reported 2019 profit of "
             "about US$78m). Those accounts were later found to be falsified.",
        financials=dict(current_assets=3600.0, current_liabilities=3150.0, total_assets=4500.0,
                        retained_earnings=1300.0, ebit=120.0, equity=1500.0, total_liabilities=3000.0),
        signals=dict(news_volume=dict(recent=7, history=_hist(6, [0, 1, -1, 2])),
                     negative_news=dict(recent=1, history=_hist(1, [0, 0, 1, -1])),
                     acra_charges=dict(recent=2, history=_hist(2, [0, 1, 0, -1])),
                     director_changes=dict(recent=0, history=_hist(0, [0, 0, 0, 1])),
                     lawsuits=dict(recent=0, history=_hist(0, [0, 1, 0, 0])))),
    SME.lower(): dict(
        note="Fictional company. Synthetic accounts.",
        financials=dict(current_assets=300.0, current_liabilities=225.0, total_assets=420.0,
                        retained_earnings=45.0, ebit=21.0, equity=150.0, total_liabilities=270.0),
        signals=dict(news_volume=dict(recent=3, history=_hist(3, [0, 1, -1, 0])),
                     negative_news=dict(recent=0, history=_hist(0, [0, 0, 1, 0])),
                     acra_charges=dict(recent=1, history=_hist(1, [0, 0, 1, -1])),
                     director_changes=dict(recent=0, history=_hist(0, [0, 0, 0, 1])),
                     lawsuits=dict(recent=0, history=_hist(0, [0, 0, 0, 0])))),
}

# ------------------------------------------------------------------ example source tables
# Ownership and officer records (ACRA business-profile style). Key: entity. Value: links to others.
OWNERSHIP = {
    OTK.lower(): {HLT.lower(): "Sister company of the borrower: both owned and controlled by the Lim family "
                               "(SGDC 36 paras 109, 260)."},
    UT.lower(): {HLT.lower(): "The terminal's parent was 41% owned by the Lim family, who control the borrower "
                              "(SGDC 36 paras 263 to 265)."},
    PCS.lower(): {},
    "blue strait lines pte ltd": {},
    "harbourside tank storage pte ltd": {},
}
# Company registers: does the buyer exist?
ENTITY_REGISTER = {
    CAO.lower(): ("live", "Registered and live; SGX-listed."),
    UNIPEC.lower(): ("live", "Registered and live."),
    NORD.lower(): ("live", "Registered and live (fictional company)."),
    "selat bunker trading pte ltd": ("struck off", "Struck off the register in 2018."),
}
# ABS Trade Finance Registry: invoices already financed elsewhere.
REGISTRY_DUPLICATES = {"KBS-INV-3391": "Invoice already financed at another participating bank on 2020-02-27."}
# Ship tracking (AIS) around the document date.
VESSEL_TRACKING = {
    "mt ocean demo (placeholder name)": ("contradicted",
        "Vessel was already laden and under way to Rotterdam, not loading at the stated port "
        "(SGDC 36 paras 385 to 386)."),
    "mt pacific heron": ("confirmed", "Vessel was alongside at the stated port on the document date."),
    "mt blue strait": ("confirmed", "Vessel was alongside at the stated port on the document date."),
}
# Terminal or warehouse operator confirmations.
TERMINAL_CONFIRMATION = {
    "harbourside tank storage pte ltd": ("confirmed", "Operator confirms the transfer between the named tanks."),
    UT.lower(): ("contradicted", "Buyer rents no tanks at this terminal (SGDC 36 para 265)."),
}
# Benchmark prices, USD/bbl, by product and month.
BENCHMARKS = {("gasoil 10ppm sulphur", "2020-02"): 62.0, ("gasoil 10ppm sulphur", "2020-03"): 42.5,
              ("jet fuel", "2020-03"): 38.0, ("fuel oil 380cst", "2020-03"): 33.0}
# Adverse news about the borrower or buyer in the last 90 days.
ADVERSE_NEWS = {"tembusu petrochem pte ltd": "Two reports of a winding-up application against the borrower."}
NEWS_COVERED = {HLT.lower(), CAO.lower(), UNIPEC.lower(), SME.lower(), NORD.lower()}


# ------------------------------------------------------------------ document reader
def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def _pdf_date(d):
    return d.strftime("%Y-%m-%d %H:%M") if isinstance(d, datetime) else "not recorded"


def read_pack(data: bytes):
    """Read a request pack PDF. Returns (fields, docs, forensics)."""
    reader = PdfReader(io.BytesIO(data))
    pages = {}
    for page in reader.pages:
        title, kv = None, {}
        for line in (page.extract_text() or "").splitlines():
            line = line.strip()
            if line.upper().startswith("DOCUMENT:"):
                title = line.split(":", 1)[1].strip()
            elif ":" in line and title:
                k, v = line.split(":", 1)
                kv[k.strip().lower()] = v.strip()
        if title:
            pages[title.lower()] = kv

    contract = pages.get("sales contract", {})
    invoice = pages.get("commercial invoice", {})
    request = pages.get("discounting request", {})
    tkey = next((k for k in pages if k not in ("sales contract", "commercial invoice", "discounting request")), None)
    transport = pages.get(tkey, {}) if tkey else {}

    docs = {}
    if contract:
        docs["Sales contract"] = dict(buyer=contract.get("buyer"), product=contract.get("product"),
                                      quantity=contract.get("quantity (bbl)"), amount=contract.get("contract value (usd)"))
    if invoice:
        docs["Commercial invoice"] = dict(buyer=invoice.get("buyer"), product=invoice.get("product"),
                                          quantity=invoice.get("quantity (bbl)"), amount=invoice.get("invoice amount (usd)"))
    if transport:
        docs[transport.get("document type") or "Transport document"] = dict(
            buyer=transport.get("consignee"), product=transport.get("product"),
            quantity=transport.get("quantity (bbl)"), amount=None)

    fields = dict(
        borrower=request.get("borrower") or contract.get("seller") or invoice.get("seller") or "",
        buyer=invoice.get("buyer") or contract.get("buyer") or "",
        silent="silent" in (request.get("facility") or "").lower(),
        amount=_num(request.get("amount requested (usd)")) or _num(invoice.get("invoice amount (usd)")) or 0.0,
        product=invoice.get("product") or contract.get("product") or "",
        quantity=_num(invoice.get("quantity (bbl)")) or 0.0,
        unit_price=_num(contract.get("unit price (usd/bbl)")) or 0.0,
        invoice_no=invoice.get("invoice no") or "",
        invoice_date=invoice.get("invoice date") or "",
        due_date=invoice.get("payment due date") or "",
        request_date=request.get("request date") or "",
        doc_type=transport.get("document type") or DOC_TYPES[0],
        issuer=transport.get("issuer") or "",
        signatory=transport.get("signed by") or "",
        vessel=transport.get("vessel") or "",
        load_port=transport.get("port of loading") or "",
        terminal=transport.get("terminal") or "",
        doc_date=transport.get("document date") or "",
    )

    meta = reader.metadata
    created = getattr(meta, "creation_date", None) if meta else None
    modified = getattr(meta, "modification_date", None) if meta else None
    producer = (getattr(meta, "producer", None) if meta else None) or "unknown"
    if created and modified and (modified - created).total_seconds() > 60:
        forensics = dict(status="fail", source="Live: file metadata",
                         detail=f"File was modified after it was created (created {_pdf_date(created)}, "
                                f"modified {_pdf_date(modified)}; producer: {producer}).")
    elif created:
        forensics = dict(status="pass", source="Live: file metadata",
                         detail=f"No modification after creation (created {_pdf_date(created)}; producer: {producer}).")
    else:
        forensics = dict(status="unchecked", source="Live: file metadata", detail="File carries no creation metadata.")
    return fields, docs, forensics


# ------------------------------------------------------------------ evidence gathering
def _linked(entity, borrower):
    """True / False / None (unknown) from ownership records."""
    e, b = (entity or "").strip().lower(), (borrower or "").strip().lower()
    if not e:
        return None, "No issuer named."
    if e == b:
        return True, "The issuer is the borrower itself."
    if e in OWNERSHIP:
        if b in OWNERSHIP[e]:
            return True, OWNERSHIP[e][b]
        return False, "No shared owners or directors with the borrower found."
    return None, "Issuer not found in ownership records."


def gather(req, overrides=None, gleif=None):
    """Collect evidence for a request. `overrides` lets the demo user set a source's
    response by hand; `gleif` is an optional live lookup callable(name) -> dict|None."""
    ov = overrides or {}
    borrower = req.get("borrower", "")
    ev = {"forensics": req.get("forensics")}

    # Issuer independence (Check 1) -------------------------------------------
    linked, detail = _linked(req.get("issuer"), borrower)
    if borrower and borrower.lower() in (req.get("signatory") or "").lower():
        detail = (detail + " " if linked else "") + "The document is signed by an employee of the borrower."
        linked = True
    src = f"{EX}: ACRA-style ownership and officer records"
    if ov.get("issuer") == "Independent":
        linked, detail, src = False, "Set by hand for this demo.", "Entered by hand"
    elif ov.get("issuer") == "Linked to borrower":
        linked, detail, src = True, "Set by hand for this demo.", "Entered by hand"
    elif ov.get("issuer") == "Unknown":
        linked, detail, src = None, "Set by hand for this demo.", "Entered by hand"
    ev["issuer_link"] = dict(linked=linked, detail=detail, source=src)

    c3 = []

    def add(key, name, status, detail, source, hard=True):
        o = ov.get(key)
        if o in ("confirmed", "unchecked", "contradicted"):
            status, detail, source = o, "Set by hand for this demo.", "Entered by hand"
        c3.append(dict(key=key, name=name, status=status, detail=detail, source=source, hard=hard))

    # 1. Registry duplicate / carrier genuineness -----------------------------
    inv = (req.get("invoice_no") or "").strip()
    if not inv:
        add("registry", "No duplicate financing in the Trade Finance Registry", "unchecked",
            "No invoice number to search.", f"{EX}: ABS Trade Finance Registry")
    elif inv in REGISTRY_DUPLICATES:
        add("registry", "No duplicate financing in the Trade Finance Registry", "contradicted",
            REGISTRY_DUPLICATES[inv], f"{EX}: ABS Trade Finance Registry")
    else:
        add("registry", "No duplicate financing in the Trade Finance Registry", "confirmed",
            f"No other financing found against invoice {inv}.", f"{EX}: ABS Trade Finance Registry")

    # 2. Did the goods move? Rule depends on the transport document type ------
    dt = req.get("doc_type") or ""
    name = "Independent proof that the goods moved"
    if dt == "Bill of lading":
        v = (req.get("vessel") or "").strip().lower()
        if not v:
            add("movement", name, "unchecked", "Bill of lading names no vessel.", f"{EX}: ship tracking (AIS)")
        elif v in VESSEL_TRACKING:
            add("movement", name, *VESSEL_TRACKING[v], f"{EX}: ship tracking (AIS)")
        else:
            add("movement", name, "unchecked", "Vessel not found in tracking data.", f"{EX}: ship tracking (AIS)")
    elif dt in ("Inter-tank transfer certificate", "Warehouse receipt"):
        t = (req.get("terminal") or req.get("issuer") or "").strip()
        t_linked, t_detail = _linked(t, borrower)
        resp = TERMINAL_CONFIRMATION.get(t.lower())
        if not t:
            add("movement", name, "unchecked", "No terminal or warehouse named.", f"{EX}: terminal operator")
        elif resp and resp[0] == "contradicted":
            add("movement", name, "contradicted", resp[1], f"{EX}: terminal operator")
        elif t_linked:
            add("movement", name, "unchecked",
                "The operator is linked to the borrower, so its confirmation does not count. " + t_detail,
                f"{EX}: terminal operator")
        elif resp:
            add("movement", name, resp[0], resp[1], f"{EX}: terminal operator")
        else:
            add("movement", name, "unchecked", "Operator has not confirmed the transfer.", f"{EX}: terminal operator")
    else:
        add("movement", name, "unchecked", f"No data source is connected for '{dt}' in this demonstrator.",
            "Not connected")

    # 3. Buyer exists and is independent --------------------------------------
    buyer = (req.get("buyer") or "").strip()
    name = "Buyer exists and is independent of the borrower"
    b_linked = buyer.lower() in OWNERSHIP and borrower.lower() in OWNERSHIP[buyer.lower()]
    live = gleif(buyer) if (gleif and buyer) else None
    if not buyer:
        add("buyer", name, "unchecked", "No buyer named.", "Company registers")
    elif b_linked:
        add("buyer", name, "unchecked", "Buyer shares owners with the borrower, so the sale is not at arm's length. "
            + OWNERSHIP[buyer.lower()][borrower.lower()], f"{EX}: ACRA-style ownership records")
    elif live:
        st_ = (live.get("status") or "").upper()
        add("buyer", name, "confirmed" if st_ == "ACTIVE" else "contradicted",
            f"LEI {live['lei']}: {live['name']} ({live.get('country', '')}), entity status {st_ or 'unknown'}.",
            "Live: GLEIF LEI register")
    elif buyer.lower() in ENTITY_REGISTER:
        s, d = ENTITY_REGISTER[buyer.lower()]
        add("buyer", name, "confirmed" if s == "live" else "contradicted", d, f"{EX}: company register")
    else:
        add("buyer", name, "unchecked", "Buyer not found in the registers searched.", "Company registers")

    # 4. Price near market -----------------------------------------------------
    name = "Price is near the market price"
    bench = BENCHMARKS.get(((req.get("product") or "").strip().lower(), str(req.get("price_date") or "")[:7]))
    up = req.get("unit_price") or 0
    if not bench or not up:
        add("price", name, "unchecked", "No benchmark for this product and month, or no unit price given.",
            f"{EX}: benchmark prices")
    else:
        diff = (up - bench) / bench
        add("price", name, "confirmed" if abs(diff) <= engine.PRICE_BAND else "contradicted",
            f"Unit price US${up:,.2f} against benchmark US${bench:,.2f} ({diff:+.1%}; allowed band "
            f"+/-{engine.PRICE_BAND:.0%}).", f"{EX}: benchmark prices")

    # 5. Adverse news (raises the score but is not an outright contradiction) --
    name = "No adverse news on the borrower or buyer"
    hit = next((ADVERSE_NEWS[n.lower()] for n in (borrower, buyer) if n and n.lower() in ADVERSE_NEWS), None)
    if hit:
        add("news", name, "contradicted", hit, f"{EX}: news screening", hard=False)
    elif borrower.lower() in NEWS_COVERED:
        add("news", name, "confirmed", "No adverse reports in the 90 days before the request.",
            f"{EX}: news screening", hard=False)
    else:
        add("news", name, "unchecked", "Company not covered by the news source.", f"{EX}: news screening", hard=False)

    ev["c3"] = c3
    return ev


def build_request(fields, docs=None, forensics=None):
    """Turn form fields into a request, attaching bank-held records for the borrower if any."""
    req = dict(fields)
    req["docs"] = docs or {}
    req["forensics"] = forensics
    req["price_date"] = fields.get("contract_date") or fields.get("invoice_date") or ""
    rec = BORROWER_RECORDS.get((fields.get("borrower") or "").strip().lower())
    if "financials" not in req:
        req["financials"] = rec["financials"] if rec else None
    if "signals" not in req:
        req["signals"] = rec["signals"] if rec else {}
    req["records_note"] = rec["note"] if rec else ""
    return req


# ------------------------------------------------------------------ background portfolio (fictional)
ENTITY_REGISTER.update({
    "lion city aviation fuels pte ltd": ("live", "Registered and live (fictional company)."),
    "marina east traders pte ltd": ("live", "Registered and live (fictional company)."),
})
OWNERSHIP.update({"bukit timah energy pte ltd": {}})
NEWS_COVERED.update({"kallang bunker supply pte ltd", "jurong delta commodities pte ltd",
                     "bukit timah energy pte ltd", "changi coast energy pte ltd"})


def _fin(ca, cl, ta, re_, ebit, eq, tl):
    return dict(current_assets=ca, current_liabilities=cl, total_assets=ta, retained_earnings=re_,
                ebit=ebit, equity=eq, total_liabilities=tl)


def _sig(news=(3, 3), neg=(0, 0), charges=(1, 1), directors=(0, 0), suits=(0, 0), short=False):
    def one(recent, base):
        return dict(recent=recent, history=_hist(base, [0, 1, -1, 0])[: (4 if short else 20)])
    return dict(news_volume=one(*news), negative_news=one(*neg), acra_charges=one(*charges),
                director_changes=one(*directors), lawsuits=one(*suits))


def background_requests():
    """Synthetic earlier requests so the reviewer queue and board view are not empty.
    Returns a list of (fields, financials, signals, officer, decision_or_None)."""
    base = dict(silent=True, product="Gasoil 10ppm sulphur", unit_price=42.4, invoice_date="2020-03-09",
                due_date="2020-04-08", request_date="2020-03-10", doc_type="Bill of lading",
                issuer="Blue Strait Lines Pte Ltd", signatory="Master, MT Blue Strait", vessel="MT Blue Strait",
                load_port="Singapore", terminal="", doc_date="2020-03-08", buyer=NORD)
    healthy = _fin(300, 210, 420, 10, 22, 150, 270)
    rows = [
        (dict(base, borrower="Jurong Delta Commodities Pte Ltd", amount=6_100_000, quantity=144_000,
              invoice_no="JDC-1182", doc_type="Warehouse receipt", issuer="Harbourside Tank Storage Pte Ltd",
              signatory="Operations, Harbourside Tank Storage", vessel="", terminal="Harbourside Tank Storage Pte Ltd"),
         healthy, _sig(), "A. Tan (GTRF)", None),
        (dict(base, borrower="Kallang Bunker Supply Pte Ltd", buyer="Selat Bunker Trading Pte Ltd",
              amount=8_400_000, quantity=198_000, invoice_no="KBS-INV-3391"),
         _fin(120, 110, 200, 15, 6, 50, 150), _sig(), "M. Rahman (GTRF)",
         ("Reject", "J. Koh (RMU)", "Invoice already financed elsewhere and the buyer is struck off. Referred to the fraud team.")),
        (dict(base, borrower="Tembusu Petrochem Pte Ltd", silent=False, amount=12_500_000, quantity=295_000,
              invoice_no="TPC-7730", buyer="Marina East Traders Pte Ltd", doc_type="Truck or rail consignment note",
              issuer="", signatory="", vessel="", product="Fuel oil 180cst", unit_price=30.0, _mismatch=True),
         _fin(140, 190, 310, -20, -9, 40, 270),
         _sig(news=(14, 4), neg=(9, 1), charges=(6, 1), directors=(4, 0), suits=(4, 0)), "A. Tan (GTRF)", None),
        (dict(base, borrower="Pasir Ris Fuels Pte Ltd", amount=2_900_000, quantity=76_000, product="Jet fuel",
              unit_price=38.4, invoice_no="PRF-0456", buyer="Lion City Aviation Fuels Pte Ltd",
              doc_type="Air waybill", issuer="", signatory="", vessel=""),
         _fin(60, 62, 110, 8, 3, 30, 80), _sig(short=True), "M. Rahman (GTRF)",
         ("Note", "M. Rahman (GTRF)", "New client with under two years of history. Air freight sample cargo; airline record requested.")),
        (dict(base, borrower="Bukit Timah Energy Pte Ltd", amount=9_000_000, quantity=212_000, invoice_no="BTE-2204",
              issuer="Bukit Timah Energy Pte Ltd", signatory="Logistics manager, Bukit Timah Energy Pte Ltd",
              vessel="MT Blue Strait"),
         healthy, _sig(), "A. Tan (GTRF)",
         ("Release", "J. Koh (RMU)", "Borrower issued its own bill of lading. Vessel position and cargo confirmed directly with the independent ship manager before release.")),
        (dict(base, borrower="Changi Coast Energy Pte Ltd", amount=18_200_000, quantity=429_000, invoice_no="CCE-9015"),
         healthy, _sig(), "M. Rahman (GTRF)",
         ("Release", "S. Nair (RMU)", "Large silent deal reviewed under R3. All five outside checks agree. Paid next business day.")),
    ]
    return rows
