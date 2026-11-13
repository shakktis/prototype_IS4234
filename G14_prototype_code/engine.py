"""Trade Authenticity Risk Scorer: scoring engine.

Pure functions. No company-specific logic: every rule works on the fields of a
request and on the evidence gathered for it. Every test result carries the document
reference it examined, what that document states, and what the evidence shows, so
the decision can be explained line by line.
"""
from statistics import mean, pstdev

WEIGHTS = {"c1": 0.30, "c2": 0.30, "c3": 0.40}
STATUS_POINTS = {"confirmed": 0, "unchecked": 50, "contradicted": 100}
RESULT_LABEL = {"confirmed": "Verified", "unchecked": "Not verified", "contradicted": "Contradicted",
                "pass": "Pass", "fail": "Fail", "adverse": "Adverse"}
BAND_ORDER = ["Low", "Medium", "High", "Critical"]
BAND_RANGE = {"Low": "0 to 29", "Medium": "30 to 49", "High": "50 to 69", "Critical": "70 to 100"}
BAND_ACTION = {
    "Low": "Payment released as normal. The result is logged.",
    "Medium": "Payment released once the submitting officer records a short note. The result is logged.",
    "High": "Payment held until a named Risk Management Unit reviewer approves it.",
    "Critical": "Payment blocked until a named Risk Management Unit reviewer clears it.",
}
Z_SAFE, Z_DISTRESS = 2.6, 1.1
MIN_HISTORY = 8            # past 90-day periods needed to judge "normal"
PRICE_BAND = 0.10          # +/- 10% of benchmark
R3_THRESHOLD = 10_000_000  # large silent deal (USD), example value
C1, C2, C3 = "Check 1: Documents", "Check 2: Company", "Check 3: Transaction"
ACCOUNTS_REF = "Borrower accounts held by the bank (not part of the request pack)"
SIGNALS_REF = "Public records on the borrower (not part of the request pack)"

SIGNALS = {
    "news_volume": "News volume",
    "negative_news": "Negative news",
    "acra_charges": "New ACRA charges",
    "director_changes": "Director changes",
    "lawsuits": "Lawsuits",
}


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def band_for(score):
    if score >= 70:
        return "Critical"
    if score >= 50:
        return "High"
    if score >= 30:
        return "Medium"
    return "Low"


def _norm(v):
    return " ".join(str(v or "").lower().replace(",", "").split())


# ---------------------------------------------------------------- Check 1
def check1(req, ev):
    refs = req.get("refs") or {}
    tests = []

    # 1.1 Consistency across the documents
    docs = req.get("docs") or {}
    mism = []
    for field in ("buyer", "product", "quantity", "amount"):
        seen = {n: _norm(d[field]) for n, d in docs.items() if d.get(field) not in (None, "")}
        if len(set(seen.values())) > 1:
            mism.append(field + " (" + "; ".join(f"{n}: {docs[n][field]}" for n in seen) + ")")
    base = dict(id="1.1", name="Documents agree with each other",
                doc_ref=refs.get("all_docs", "Request documents"),
                stated="Buyer, product, quantity and amount as stated in each document.")
    if not docs:
        tests.append(dict(base, result="unchecked", points=0, found="No document pack was read, so the documents "
                          "could not be compared.", source="Document reader"))
    elif mism:
        tests.append(dict(base, result="fail", points=30, found="The documents disagree on " + ", ".join(mism) + ".",
                          source="Live: document reader"))
    else:
        tests.append(dict(base, result="pass", points=0, found="Buyer, product, quantity and amount match across "
                          + ", ".join(docs.keys()) + ".", source="Live: document reader"))

    # 1.2 Signs of editing in file metadata
    f = ev.get("forensics") or {"status": "unchecked", "detail": "No file was supplied, so metadata could not be inspected."}
    tests.append(dict(id="1.2", name="No sign of editing in the file metadata",
                      doc_ref=refs.get("file", "Request pack file"),
                      stated="File creation and modification records.",
                      result={"pass": "pass", "fail": "fail"}.get(f["status"], "unchecked"),
                      points=30 if f["status"] == "fail" else 0,
                      found=f["detail"], source=f.get("source", "File metadata")))

    # 1.3 Issuer of the transport document is independent of the borrower
    il = ev.get("issuer_link") or {"linked": None, "detail": "Ownership records were not searched."}
    res, pts = ("fail", 40) if il["linked"] is True else ("pass", 0) if il["linked"] is False else ("unchecked", 0)
    tests.append(dict(id="1.3", name="Transport document issuer is independent of the borrower",
                      doc_ref=il.get("doc_ref", "Transport document"), stated=il.get("stated", ""),
                      result=res, points=pts, found=il["detail"], source=il.get("source", "Ownership records"),
                      issuer_test=True))

    return dict(score=clamp(sum(t["points"] for t in tests)), tests=tests)


# ---------------------------------------------------------------- Check 2
def altman_z(fin):
    """Altman Z''-score for private, non-manufacturing firms (Altman, 2000)."""
    ta, tl = fin["total_assets"], fin["total_liabilities"]
    if not ta or not tl:
        return None
    x1 = (fin["current_assets"] - fin["current_liabilities"]) / ta
    x2 = fin["retained_earnings"] / ta
    x3 = fin["ebit"] / ta
    x4 = fin["equity"] / tl
    z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
    return dict(z=z, x1=x1, x2=x2, x3=x3, x4=x4)


def health_from_z(z):
    """0 at Z'' <= 1.1 (distressed), 100 at Z'' >= 2.6 (safe), straight line between."""
    return clamp((z - Z_DISTRESS) / (Z_SAFE - Z_DISTRESS) * 100)


def signal_deviation(recent, history):
    """How far the last 90 days sit above the company's own past 90-day periods."""
    if history is None or len(history) < MIN_HISTORY or recent is None:
        return None
    mu, sd = mean(history), max(pstdev(history), 1.0)
    z = (recent - mu) / sd
    return dict(z=z, mean=mu, sd=sd, score=clamp(z / 3 * 100))


def check2(req, ev):
    fin = req.get("financials")
    az = altman_z(fin) if fin else None
    if az:
        health = health_from_z(az["z"])
        zone = "safe zone" if az["z"] >= Z_SAFE else "distress zone" if az["z"] <= Z_DISTRESS else "grey zone"
        health_detail = f"Altman Z'' = {az['z']:.2f} ({zone}). Financial health {health:.0f} out of 100."
    else:
        health, zone = 50.0, None
        health_detail = "The bank holds no accounts for this borrower. Financial health could not be assessed and is scored 50."

    rows, scores = [], []
    sig = req.get("signals") or {}
    for key, label in SIGNALS.items():
        s = sig.get(key) or {}
        d = signal_deviation(s.get("recent"), s.get("history"))
        if d is None:
            rows.append(dict(signal=label, recent=s.get("recent"), normal=None, score=50.0,
                             reading="Not verified: fewer than %d earlier periods" % MIN_HISTORY))
            scores.append(50.0)
        else:
            rows.append(dict(signal=label, recent=s["recent"], normal=round(d["mean"], 1), score=round(d["score"], 0),
                             reading="Unusual for this company" if d["score"] >= 50 else "Within its normal range"))
            scores.append(d["score"])
    deviation = mean(scores)
    score = 0.5 * (100 - health) + 0.5 * deviation
    return dict(score=clamp(score), health=health, health_detail=health_detail, z=az, zone=zone,
                deviation=deviation, signals=rows, has_accounts=bool(az))


# ---------------------------------------------------------------- Check 3
def check3(req, ev):
    subs = []
    for i, s in enumerate(ev.get("c3", []), start=1):
        s = dict(s, id=f"3.{i}")
        s["points"] = STATUS_POINTS[s["status"]]
        subs.append(s)
    score = mean(s["points"] for s in subs) if subs else 50.0
    return dict(score=score, subs=subs)


# ---------------------------------------------------------------- Overall
def score_request(req, ev):
    c1, c2, c3 = check1(req, ev), check2(req, ev), check3(req, ev)
    overall = WEIGHTS["c1"] * c1["score"] + WEIGHTS["c2"] * c2["score"] + WEIGHTS["c3"] * c3["score"]
    base_band = band = band_for(overall)
    basis = "Weighted score"
    n3 = max(len(c3["subs"]), 1)

    hard = [s for s in c3["subs"] if s["status"] == "contradicted" and s.get("hard", True)]
    issuer_fail = next((t for t in c1["tests"] if t.get("issuer_test") and t["result"] == "fail"), None)
    policy_hold = bool(req.get("silent")) and float(req.get("amount") or 0) >= R3_THRESHOLD

    # ---- adverse findings, most decisive first
    raw = []
    for s in c3["subs"]:
        if s["status"] == "contradicted":
            pts = WEIGHTS["c3"] * 100 / n3
            decisive = s.get("hard", True)
            effect = (f"Decisive. Override rule 1 sets the band to Critical. Also contributes {pts:.0f} points to the weighted score."
                      if decisive else f"Contributes {pts:.0f} points to the weighted score. Not treated as a contradiction of the sale.")
            raw.append((3000 if decisive else pts, dict(check=C3, test_id=s["id"], test=s["name"], result="contradicted" if decisive else "adverse",
                                                         doc_ref=s["doc_ref"], stated=s["stated"], found=s["detail"],
                                                         source=s["source"], effect=effect, decisive=decisive)))
    for t in c1["tests"]:
        if t["result"] == "fail":
            pts = WEIGHTS["c1"] * t["points"]
            if t.get("issuer_test"):
                effect = (f"Override rule 2 sets the band to at least High. Also contributes {pts:.0f} points to the weighted score.")
            else:
                effect = f"Contributes {pts:.0f} points to the weighted score."
            raw.append((2000 if t.get("issuer_test") else pts,
                        dict(check=C1, test_id=t["id"], test=t["name"], result="fail", doc_ref=t["doc_ref"],
                             stated=t["stated"], found=t["found"], source=t["source"], effect=effect,
                             decisive=bool(t.get("issuer_test")) and not hard)))
    if c2["has_accounts"] and c2["health"] < 50:
        pts = WEIGHTS["c2"] * 0.5 * (100 - c2["health"])
        raw.append((pts, dict(check=C2, test_id="2.1", test="Financial health", result="adverse", doc_ref=ACCOUNTS_REF,
                              stated="Seven balance-sheet figures used in the Altman Z'' formula.",
                              found=c2["health_detail"], source="Example data: accounts held by the bank",
                              effect=f"Contributes {pts:.0f} points to the weighted score.", decisive=False)))
    for r in c2["signals"]:
        if r["normal"] is not None and r["score"] >= 50:
            pts = WEIGHTS["c2"] * 0.5 * r["score"] / len(SIGNALS)
            raw.append((pts, dict(check=C2, test_id="2.2", test=f"Behaviour: {r['signal'].lower()}", result="adverse",
                                  doc_ref=SIGNALS_REF, stated=f"Normal level for this company: {r['normal']} per 90 days.",
                                  found=f"{r['recent']} in the last 90 days.",
                                  source="Example data: news, ACRA and court records",
                                  effect=f"Contributes {pts:.0f} points to the weighted score.", decisive=False)))
    raw.sort(key=lambda x: -x[0])
    findings = [dict(f, id=f"F{i}") for i, (_, f) in enumerate(raw, start=1)]

    # ---- items that could not be verified
    unverified = []
    for s in c3["subs"]:
        if s["status"] == "unchecked":
            unverified.append(dict(check=C3, test_id=s["id"], test=s["name"], doc_ref=s["doc_ref"], reason=s["detail"],
                                   source=s["source"], effect=f"Scored 50, not 0. Contributes {WEIGHTS['c3'] * 50 / n3:.0f} points."))
    for t in c1["tests"]:
        if t["result"] == "unchecked":
            unverified.append(dict(check=C1, test_id=t["id"], test=t["name"], doc_ref=t["doc_ref"], reason=t["found"],
                                   source=t["source"], effect="No points added. Reported so the gap is visible."))
    if not c2["has_accounts"]:
        unverified.append(dict(check=C2, test_id="2.1", test="Financial health", doc_ref=ACCOUNTS_REF,
                               reason="The bank holds no accounts for this borrower.", source="Accounts held by the bank",
                               effect=f"Scored 50. Contributes {WEIGHTS['c2'] * 0.5 * 50:.1f} points."))
    short = [r["signal"] for r in c2["signals"] if r["normal"] is None]
    if short:
        unverified.append(dict(check=C2, test_id="2.2", test="Behaviour: " + ", ".join(x.lower() for x in short),
                               doc_ref=SIGNALS_REF, reason=f"Fewer than {MIN_HISTORY} earlier 90-day periods on record.",
                               source="Example data: news, ACRA and court records",
                               effect=f"Each scored 50. Contributes {WEIGHTS['c2'] * 0.5 * 50 * len(short) / len(SIGNALS):.1f} points."))

    # ---- how the band was determined
    steps = [f"Weighted score: 0.3 × {c1['score']:.0f} (Check 1) + 0.3 × {c2['score']:.0f} (Check 2) + 0.4 × "
             f"{c3['score']:.0f} (Check 3) = {overall:.1f}. On the score alone this is the {base_band} band "
             f"({BAND_RANGE[base_band]})."]
    ids = lambda pred: ", ".join(f["id"] for f in findings if pred(f))
    if hard:
        band = "Critical"
        basis = "Override rule 1: independent evidence contradicts the transaction"
        steps.append("Override rule 1 (contradiction): applies. " + ids(lambda f: f["check"] == C3 and f["result"] == "contradicted")
                     + " records independent evidence that contradicts the transaction as documented. "
                       "The band is set to Critical whatever the weighted score.")
    else:
        steps.append("Override rule 1 (contradiction): does not apply. No independent source contradicts the transaction.")
    if issuer_fail:
        fid = ids(lambda f: f["test_id"] == "1.3")
        if hard:
            steps.append(f"Override rule 2 (linked issuer): applies ({fid}), but the band is already Critical under rule 1.")
        elif BAND_ORDER.index(band) < BAND_ORDER.index("High"):
            band = "High"
            basis = "Override rule 2: the transport document was issued or signed by a party linked to the borrower"
            steps.append(f"Override rule 2 (linked issuer): applies ({fid}). A transport document issued or signed by a "
                         "party linked to the borrower does not count as independent proof (recommendation R2). "
                         "The band is raised to High.")
        else:
            steps.append(f"Override rule 2 (linked issuer): applies ({fid}), but the weighted score already places the request at {band}.")
    else:
        steps.append("Override rule 2 (linked issuer): does not apply.")
    if policy_hold:
        steps.append(f"Policy rule R3 (large silent deal): applies. The facility is silent and the amount is "
                     f"US${R3_THRESHOLD / 1e6:.0f} million or more, so a named reviewer must approve it and it cannot "
                     "be paid on the day of the request.")
    else:
        steps.append("Policy rule R3 (large silent deal): does not apply.")

    if band == "Critical":
        status = "Blocked"
    elif band == "High":
        status = "Held"
    elif policy_hold:
        status = "Held (large silent deal)"
    elif band == "Medium":
        status = "Awaiting officer note"
    else:
        status = "Paid"
    action = BAND_ACTION[band]
    if status == "Held (large silent deal)":
        action = "Payment held until a named Risk Management Unit reviewer approves it, under policy rule R3."
    steps.append(f"Outcome: {band} band. {action}")

    # ---- plain-language explanation of a band or hold that the score alone would not give
    explanation = []
    if band != base_band:
        lead = next((f for f in findings if f["decisive"]), findings[0] if findings else None)
        pts = (WEIGHTS["c3"] * 100 / n3) if hard else WEIGHTS["c1"] * 40
        explanation = [
            ("Weighted score", f"{overall:.0f} out of 100. On the score alone the request would be {base_band} "
                               f"({BAND_RANGE[base_band]})."),
            ("Band applied", f"{band}, under {basis[0].lower() + basis[1:]}."),
            ("Decisive finding", f"{lead['id']}: {lead['check']}, test {lead['test_id']} ({lead['test']})."),
            ("Document examined", lead["doc_ref"] + "."),
            ("The document states", lead["stated"]),
            ("The evidence shows", f"{lead['found']} Source: {lead['source']}."),
            ("Why the rule applies",
             "A transaction that an independent source contradicts cannot be treated as low risk, whatever the other "
             "tests show. Override rule 1 therefore sets the band to Critical." if hard else
             "A transport document issued or signed by a party linked to the borrower is not independent proof that "
             "the goods moved (recommendation R2). Override rule 2 therefore sets the band to at least High."),
            ("Why the score is low", f"The score is a weighted average across all the tests in the three checks. This finding moves it by only "
                                     f"{pts:.0f} points and the remaining tests found little or nothing adverse. The "
                                     "override rules exist so that one decisive finding is not averaged away."),
        ]
    elif status == "Held (large silent deal)":
        explanation = [
            ("Weighted score", f"{overall:.0f} out of 100, which is the {band} band. No override rule applies."),
            ("Why payment is held", f"The facility is silent and the amount is US${R3_THRESHOLD / 1e6:.0f} million or more. "
                                    "Policy rule R3 requires a named Risk Management Unit reviewer for such a request "
                                    "and does not allow payment on the day it is received."),
            ("What the reviewer decides", "Whether the evidence gathered is sufficient to release the payment, given "
                                          "that the buyer cannot be asked to confirm the sale."),
        ]

    return dict(c1=c1, c2=c2, c3=c3, overall=round(overall, 1), base_band=base_band, band=band, basis=basis,
                steps=steps, explanation=explanation, findings=findings, unverified=unverified,
                policy_hold=policy_hold, status=status, action=action,
                needs_review=status in ("Blocked", "Held", "Held (large silent deal)"))
