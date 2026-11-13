"""Trade Authenticity Risk Scorer: scoring engine.

Pure functions. No company-specific logic: every rule works on the fields of a
request and on the evidence gathered for it.
"""
from statistics import mean, pstdev

WEIGHTS = {"c1": 0.30, "c2": 0.30, "c3": 0.40}
STATUS_POINTS = {"confirmed": 0, "unchecked": 50, "contradicted": 100}
STATUS_LABEL = {
    "confirmed": "Outside evidence agrees",
    "unchecked": "Could not check",
    "contradicted": "Outside evidence disagrees",
}
BAND_ORDER = ["Low", "Medium", "High", "Critical"]
BAND_ACTION = {
    "Low": "Paid as normal. Logged only.",
    "Medium": "Paid once the officer records a short note. Logged only.",
    "High": "Held until a named Risk Management Unit reviewer approves.",
    "Critical": "Blocked until a named Risk Management Unit reviewer clears it.",
}
Z_SAFE, Z_DISTRESS = 2.6, 1.1
MIN_HISTORY = 8          # past 90-day periods needed to judge "normal"
PRICE_BAND = 0.10        # +/- 10% of benchmark
R3_THRESHOLD = 10_000_000  # large silent deal (USD), example value

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
    tests = []

    # 1a. Consistency across the documents
    docs = req.get("docs") or {}
    mism = []
    for field in ("buyer", "product", "quantity", "amount"):
        seen = {n: _norm(d[field]) for n, d in docs.items() if d.get(field) not in (None, "")}
        if len(set(seen.values())) > 1:
            mism.append(field + " (" + "; ".join(f"{n}: {docs[n][field]}" for n in seen) + ")")
    if not docs:
        tests.append(dict(name="Documents agree with each other", result="unchecked", points=0,
                          detail="No document pack was read.", source="Document reader"))
    elif mism:
        tests.append(dict(name="Documents agree with each other", result="fail", points=30,
                          detail="Mismatch on " + ", ".join(mism), source="Live: document reader"))
    else:
        tests.append(dict(name="Documents agree with each other", result="pass", points=0,
                          detail="Buyer, product, quantity and amount match across "
                                 + ", ".join(docs.keys()) + ".", source="Live: document reader"))

    # 1b. Signs of editing in file metadata
    f = ev.get("forensics") or {"status": "unchecked", "detail": "No file to inspect."}
    tests.append(dict(name="No signs of editing in file metadata",
                      result={"pass": "pass", "fail": "fail"}.get(f["status"], "unchecked"),
                      points=30 if f["status"] == "fail" else 0,
                      detail=f["detail"], source=f.get("source", "File metadata")))

    # 1c. Issuer of the transport document is independent of the borrower
    il = ev.get("issuer_link") or {"linked": None, "detail": "Ownership not looked up."}
    if il["linked"] is True:
        res, pts = "fail", 40
    elif il["linked"] is False:
        res, pts = "pass", 0
    else:
        res, pts = "unchecked", 0
    tests.append(dict(name="Transport document issuer is independent of the borrower",
                      result=res, points=pts, detail=il["detail"],
                      source=il.get("source", "Ownership records")))

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
        zone = "safe" if az["z"] >= Z_SAFE else "distressed" if az["z"] <= Z_DISTRESS else "grey zone"
        health_detail = f"Z'' = {az['z']:.2f} ({zone}). Health {health:.0f}/100."
    else:
        health, health_detail = 50.0, "No accounts held, so health could not be checked (scored 50)."

    rows, scores = [], []
    sig = req.get("signals") or {}
    for key, label in SIGNALS.items():
        s = sig.get(key) or {}
        d = signal_deviation(s.get("recent"), s.get("history"))
        if d is None:
            rows.append(dict(signal=label, recent=s.get("recent"), normal=None, score=50.0,
                             note="Too little history: could not check"))
            scores.append(50.0)
        else:
            rows.append(dict(signal=label, recent=s["recent"], normal=round(d["mean"], 1),
                             score=round(d["score"], 0),
                             note="Unusual for this company" if d["score"] >= 50 else "Within its normal range"))
            scores.append(d["score"])
    deviation = mean(scores)
    score = 0.5 * (100 - health) + 0.5 * deviation
    return dict(score=clamp(score), health=health, health_detail=health_detail, z=az,
                deviation=deviation, signals=rows, has_accounts=bool(az))


# ---------------------------------------------------------------- Check 3
def check3(req, ev):
    subs = []
    for s in ev.get("c3", []):
        s = dict(s)
        s["points"] = STATUS_POINTS[s["status"]]
        subs.append(s)
    score = mean(s["points"] for s in subs) if subs else 50.0
    return dict(score=score, subs=subs)


# ---------------------------------------------------------------- Overall
def score_request(req, ev):
    c1, c2, c3 = check1(req, ev), check2(req, ev), check3(req, ev)
    overall = WEIGHTS["c1"] * c1["score"] + WEIGHTS["c2"] * c2["score"] + WEIGHTS["c3"] * c3["score"]
    base_band = band = band_for(overall)
    overrides = []

    contradicted = [s for s in c3["subs"] if s["status"] == "contradicted" and s.get("hard", True)]
    issuer_linked = any(t["result"] == "fail" and "issuer" in t["name"].lower() for t in c1["tests"])
    if contradicted:
        band = "Critical"
        overrides.append("Outside evidence contradicts the deal ("
                         + "; ".join(s["name"] for s in contradicted) + "): straight to Critical.")
    elif issuer_linked and BAND_ORDER.index(band) < BAND_ORDER.index("High"):
        band = "High"
        overrides.append("Transport document issuer is linked to the borrower, so it does not count "
                         "as proof (R2): at least High.")

    policy_hold = bool(req.get("silent")) and float(req.get("amount") or 0) >= R3_THRESHOLD
    if policy_hold:
        overrides.append(f"Large silent deal (US${R3_THRESHOLD/1e6:.0f}m or more): needs a named reviewer "
                         "and is never paid the same day (R3).")

    # Reasons, ranked by the points each contributes to the overall score
    reasons = []
    for s in c3["subs"]:
        if s["status"] != "confirmed":
            reasons.append((WEIGHTS["c3"] * s["points"] / max(len(c3["subs"]), 1)
                            + (1000 if s["status"] == "contradicted" and s.get("hard", True) else 0),
                            f"{s['name']}: {STATUS_LABEL[s['status']].lower()}. {s['detail']}", s["source"]))
    for t in c1["tests"]:
        if t["result"] == "fail":
            reasons.append((WEIGHTS["c1"] * t["points"] + (500 if "issuer" in t["name"].lower() else 0),
                            f"{t['name']}: failed. {t['detail']}", t["source"]))
    if c2["health"] < 50:
        reasons.append((WEIGHTS["c2"] * 0.5 * (100 - c2["health"]), "Company health is weak. " + c2["health_detail"],
                        "Accounts held by the bank"))
    for r in c2["signals"]:
        if r["score"] >= 50 and r["normal"] is not None:
            reasons.append((WEIGHTS["c2"] * 0.5 * r["score"] / 5,
                            f"{r['signal']} is unusual for this company: {r['recent']} in the last 90 days "
                            f"against a normal of {r['normal']}.", "Public signals"))
    reasons.sort(key=lambda r: -r[0])
    reasons = [dict(text=r[1], source=r[2]) for r in reasons]

    unchecked = [f"{s['name']}: {s['detail']}" for s in c3["subs"] if s["status"] == "unchecked"]
    unchecked += [f"{t['name']}: {t['detail']}" for t in c1["tests"] if t["result"] == "unchecked"]
    if not c2["has_accounts"]:
        unchecked.append("Company health: no accounts held.")
    unchecked += [f"{r['signal']}: too little history." for r in c2["signals"] if r["normal"] is None]

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

    return dict(c1=c1, c2=c2, c3=c3, overall=round(overall, 1), base_band=base_band, band=band,
                overrides=overrides, policy_hold=policy_hold, reasons=reasons, unchecked=unchecked,
                status=status, action=BAND_ACTION[band], needs_review=status in ("Blocked", "Held", "Held (large silent deal)"))
