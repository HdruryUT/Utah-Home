#!/usr/bin/env python3
"""Score Utah County listings and keep a running history.

Usage:
  python3 scripts/score.py --raw data/raw/2026-11-07.json \
      --db data/listings.json --out docs/dashboard.json [--today 2026-11-07]

--raw   JSON list of listings found in today's scan (see SCAN.md for fields).
--db    Running history of every listing ever seen. Created if missing.
--out   Flat rows for the dashboard: every non-gone listing, scored.

No third-party dependencies.
"""
import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CRITERIA = ROOT / "criteria.json"


def load_criteria(path=DEFAULT_CRITERIA):
    with open(path) as f:
        return json.load(f)


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def norm_address(addr):
    a = (addr or "").lower()
    a = re.sub(r"[^a-z0-9 ]", " ", a)
    repl = {"north": "n", "south": "s", "east": "e", "west": "w", "street": "st",
            "avenue": "ave", "drive": "dr", "road": "rd", "lane": "ln", "circle": "cir",
            "court": "ct", "place": "pl", "boulevard": "blvd", "unit": "", "apt": ""}
    words = [repl.get(w, w) for w in a.split()]
    return " ".join(w for w in words if w)


def listing_id(listing):
    return norm_address(f"{listing.get('address', '')} {listing.get('city', '')}").replace(" ", "-")


def text_blob(listing):
    parts = [listing.get("description", ""), " ".join(listing.get("keywords", []) or []),
             listing.get("property_type", "")]
    return " ".join(p for p in parts if p).lower()


def passes_filters(listing, c):
    """Return (ok, reason)."""
    req = c["requirements"]
    price = listing.get("price")
    if not price:
        return False, "no price"
    if price > c["budget"]["stretch_max"]:
        return False, "over stretch budget"
    if (listing.get("beds") or 0) < req["min_beds"]:
        return False, "too few beds"
    ptype = (listing.get("property_type") or "").lower().replace("-", "_").replace(" ", "_")
    if ptype not in req["property_types_allowed"]:
        return False, f"property type {ptype or 'unknown'}"
    lot = listing.get("lot_acres")
    if lot is not None and lot < req.get("min_lot_acres", 0):
        return False, f"lot {lot} ac: attached or townhome-style"
    blob = text_blob(listing)
    for kw in req["reject_keywords"]:
        if kw in blob:
            return False, f"keyword: {kw}"
    return True, ""


def rehab_tier(listing, c):
    blob = text_blob(listing)
    kws = c["condition_keywords"]
    if any(k in blob for k in kws["heavy"]):
        return "heavy"
    if any(k in blob for k in kws.get("medium", [])):
        return "medium"
    if any(k in blob for k in kws["light"]):
        return "light"
    yb = listing.get("year_built")
    if yb and yb < 1980:
        return "medium"
    if yb and yb >= 2000:
        return "light"
    return "medium"


def region_for(city, c):
    for region, cities in c["regions"].items():
        if city in cities:
            return region
    return "Other"


def city_market(city, c):
    m = c["city_market"]
    return m.get(city, m["_default"])


def adjusted_ppsf(listing, c):
    """City median $/sqft, scaled up for smaller-than-typical homes (and down for larger)."""
    f = c["flip"]
    mkt = city_market(listing.get("city"), c)
    sqft = listing.get("sqft")
    if not sqft:
        return mkt["ppsf"]
    lo, hi = f["size_adjust_range"]
    factor = clamp((sqft / mkt["typical_sqft"]) ** -f["size_adjust_exponent"], lo, hi)
    return mkt["ppsf"] * factor


def flip_estimate(listing, c):
    f = c["flip"]
    median = round(adjusted_ppsf(listing, c))
    sqft = listing.get("sqft") or 0
    price = listing["price"]
    tier = rehab_tier(listing, c)
    if not sqft:
        return {"tier": tier, "median_ppsf": median, "arv": None, "rehab": None,
                "net_profit": None, "roi_pct": None, "market_value": None}
    rehab = sqft * f["rehab_per_sqft"][tier]
    arv_comps = sqft * median * f["arv_premium_over_median"]
    arv_value_add = price + rehab * f["rehab_value_multiple"][tier]
    arv = min(max(arv_comps, arv_value_add), price * f["arv_max_multiple_of_price"])
    selling = arv * f["selling_cost_pct"]
    buy_hold = price * f["buy_and_hold_cost_pct"]
    net = arv - price - rehab - selling - buy_hold
    invested = price + rehab + buy_hold
    return {
        "tier": tier,
        "median_ppsf": median,
        "market_value": round(sqft * median),
        "arv": round(arv),
        "rehab": round(rehab),
        "selling_costs": round(selling),
        "buy_hold_costs": round(buy_hold),
        "net_profit": round(net),
        "roi_pct": round(100 * net / invested, 1) if invested else None,
    }


def live_in_estimate(listing, flip, c):
    """Live-in flip: buy, fix up while living there for N years, then sell.
    Profit = value after N years - price - rehab - buying closing costs - selling costs.
    Mortgage interest is left out: it roughly replaces the rent you'd pay anyway."""
    li, by = c["live_in"], c["buyer"]
    price = listing["price"]
    base = flip.get("arv") or price
    rehab = flip.get("rehab") or 0
    future = base * (1 + li["annual_appreciation"]) ** li["years"]
    buy_closing = price * by["closing_cost_pct"]
    gain = future - price - rehab - buy_closing - future * c["flip"]["selling_cost_pct"]
    # Highest purchase price that still clears target_profit, holding the home's value fixed.
    target = li.get("target_profit", 40000)
    max_offer = (future * (1 - c["flip"]["selling_cost_pct"]) - rehab - target) / (1 + by["closing_cost_pct"])
    return {"years": li["years"], "future_value": round(future), "equity_gain": round(gain),
            "target_profit": target, "max_offer": round(max_offer / 1000) * 1000}


def affordability(listing, flip, c):
    by = c["buyer"]
    price = listing["price"]
    loan = price * (1 - by["down_payment_pct"])
    r = by["mortgage_rate"] / 12
    n = by["term_years"] * 12
    pi = loan * r / (1 - (1 + r) ** -n)
    tax = price * by["property_tax_rate"] / 12
    ins = by["insurance_annual"] / 12
    pmi = loan * by["pmi_rate"] / 12 if by["down_payment_pct"] < 0.2 else 0
    hoa = listing.get("hoa_monthly") or 0
    monthly = pi + tax + ins + pmi + hoa
    level = ("comfortable" if monthly <= by["comfortable_monthly_payment"]
             else "tight" if monthly <= by["max_monthly_payment"] else "high")
    cash = price * (by["down_payment_pct"] + by["closing_cost_pct"]) + (flip.get("rehab") or 0)
    return {"monthly": round(monthly), "payment_level": level, "cash_needed": round(cash),
            "cash_ok": cash <= by["cash_available"]}


def comfortable_price(c):
    """Highest price whose payment (no HOA) stays at the comfortable level, rounded down to $5k."""
    lo, hi = 50000, 1000000
    while hi - lo > 1000:
        mid = (lo + hi) / 2
        a = affordability({"price": mid}, {}, c)
        lo, hi = (mid, hi) if a["payment_level"] == "comfortable" else (lo, mid)
    return int(lo // 5000 * 5000)


def fit_score(listing, flip, c):
    w = c["weights"]
    b = c["budget"]
    by = c["buyer"]
    price = listing["price"]
    pts = {}
    live = live_in_estimate(listing, flip, c)
    aff = affordability(listing, flip, c)

    pts["profit"] = w["profit"] * clamp(live["equity_gain"] / c["flip"]["full_points_live_in_gain"])

    over = by["max_monthly_payment"] * 1.08
    pay = clamp((over - aff["monthly"]) / (over - by["comfortable_monthly_payment"]))
    pts["affordability"] = w["affordability"] * (0.6 * pay + 0.4 * (1.0 if aff["cash_ok"] else 0.0))

    beds = listing.get("beds") or 0
    baths = listing.get("baths") or 0
    layout = {2: 0.35, 3: 0.75}.get(beds, 0.85 if beds >= 4 else 0)
    layout += 0.25 if baths >= 2 else (0.15 if baths >= 1.5 else 0)
    pts["resale_layout"] = w["resale_layout"] * clamp(layout)

    sqft = listing.get("sqft")
    if sqft:
        ratio = (price / sqft) / flip["median_ppsf"]
        pts["value_vs_market"] = w["value_vs_market"] * clamp((1.2 - ratio) / 0.5)
    else:
        pts["value_vs_market"] = w["value_vs_market"] * 0.3

    pts["condition"] = w["condition"] * c["condition_scores"][flip["tier"]]
    pts["price"] = w["price"] * clamp((b["stretch_max"] - price) / (b["stretch_max"] - b["price_floor_for_scoring"]))

    lot = listing.get("lot_acres") or 0
    lg = (0.5 if listing.get("garage") else 0) + (0.5 if lot >= 0.2 else 0.3 if lot >= 0.12 else 0)
    pts["lot_garage"] = w["lot_garage"] * lg

    hoa = listing.get("hoa_monthly") or 0
    pts["hoa"] = w["hoa"] * (1.0 if hoa == 0 else 0.5 if hoa <= 50 else 0)

    dom = listing.get("days_on_market")
    pts["days_on_market"] = w["days_on_market"] * (1.0 if dom and dom >= 45 else 0.5 if dom and dom >= 21 else 0)

    blob = text_blob(listing)
    penalty = sum(p for kw, p in c.get("flag_penalties", {}).items() if kw in blob)
    if penalty:
        pts["dealbreakers"] = -penalty
    total = max(0, min(100, round(sum(pts.values()))))
    return total, {k: round(v, 1) for k, v in pts.items()}


def money(n):
    return f"${n / 1000:.0f}k" if abs(n) >= 1000 else f"${n:.0f}"


def pros_cons(listing, flip, c):
    pros, cons = [], []
    b = c["budget"]
    price = listing["price"]
    under = b["target_max"] - price
    if under < 0:
        cons.append(f"Stretch: {money(-under)} over your $380k target")

    sqft = listing.get("sqft")
    if sqft:
        ppsf = price / sqft
        ratio = ppsf / flip["median_ppsf"]
        if ratio <= 0.9:
            pros.append(f"${ppsf:.0f}/sqft vs ~${flip['median_ppsf']} {listing.get('city')} median ({(1 - ratio) * 100:.0f}% below)")
        elif ratio >= 1.1:
            cons.append(f"${ppsf:.0f}/sqft is above the ~${flip['median_ppsf']} {listing.get('city')} median")
        if sqft < 900:
            cons.append(f"Small: {sqft:,} sqft")
        elif sqft >= 1600:
            pros.append(f"Roomy: {sqft:,} sqft")
    else:
        cons.append("Square footage not listed")

    beds, baths = listing.get("beds") or 0, listing.get("baths") or 0
    if baths and baths < 1.5 and beds != 2:
        cons.append("Only 1 bathroom: adding a second is a common value-add")

    lot = listing.get("lot_acres")
    if lot and lot >= 0.2:
        pros.append(f"Big lot: {lot:.2f} ac")
    elif lot and lot < 0.1:
        cons.append(f"Small lot: {lot:.2f} ac")
    if listing.get("garage"):
        pros.append("Has a garage")
    elif listing.get("garage") is False:
        cons.append("No garage")

    yb = listing.get("year_built")
    if yb and yb < 1978:
        cons.append(f"Built {yb}: check for lead paint, the electrical, and the sewer line")
    elif yb and yb >= 2005:
        pros.append(f"Newer build ({yb})")

    tier = flip["tier"]
    if tier == "heavy":
        cons.append("Heavy fixer: may be hard to live in or finance while you work on it")

    hoa = listing.get("hoa_monthly") or 0
    if hoa:
        cons.append(f"HOA ${hoa}/mo")
    dom = listing.get("days_on_market")
    if dom and dom >= 45:
        pros.append(f"On market {dom} days: room to negotiate")
    if listing.get("price_drop"):
        pros.append(f"Price dropped {money(listing['price_drop'])}")

    blob = text_blob(listing)
    for side, out in (("pros", pros), ("cons", cons)):
        for kw, msg in c.get("flag_keywords", {}).get(side, {}).items():
            if kw in blob and msg not in out:
                out.append(msg)

    live = live_in_estimate(listing, flip, c)
    yrs = live["years"]
    if live["equity_gain"] >= 60000:
        pros.append(f"Strong profit potential: ~{money(live['equity_gain'])} after fixing up and selling in {yrs} yrs")
    elif live["equity_gain"] < 20000:
        cons.append(f"Thin profit: only ~{money(live['equity_gain'])} after fixing up and selling in {yrs} yrs")
    if live["max_offer"] >= price:
        pros.append(f"Clears ~{money(live['target_profit'])} profit even at asking price")
    elif live["max_offer"] >= price * 0.9:
        pros.append(f"Offer {money(live['max_offer'])} or less to clear ~{money(live['target_profit'])} profit")
    if tier == "medium":
        pros.append("Dated but livable: room to add value with cosmetic updates")
    elif tier == "light" and (not yb or yb < 2005):
        cons.append("Already updated: little left for you to add value")

    aff = affordability(listing, flip, c)
    by = c["buyer"]
    if aff["payment_level"] == "comfortable":
        pros.append(f"Payment ~${aff['monthly']:,}/mo: comfortable for your budget")
    elif aff["payment_level"] == "tight":
        cons.append(f"Payment ~${aff['monthly']:,}/mo: doable but tight")
    else:
        cons.append(f"Payment ~${aff['monthly']:,}/mo: likely more than you'd want to carry")
    if not aff["cash_ok"]:
        cons.append(f"Needs ~{money(aff['cash_needed'])} cash to buy and fix: more than your cash budget")

    if beds == 2 and baths < 1.5:
        cons.append("2 bed / 1 bath: fewer buyers when you sell")
    elif beds >= 3 and baths >= 2:
        pros.append("3+ bed / 2 bath: the layout most buyers want when you sell")
    return pros, cons


def score_listing(listing, c):
    flip = flip_estimate(listing, c)
    score, breakdown = fit_score(listing, flip, c)
    pros, cons = pros_cons(listing, flip, c)
    live = live_in_estimate(listing, flip, c)
    aff = affordability(listing, flip, c)
    sqft = listing.get("sqft")
    return {
        "id": listing_id(listing),
        "address": listing.get("address"),
        "city": listing.get("city"),
        "region": region_for(listing.get("city"), c),
        "price": listing["price"],
        "beds": listing.get("beds"),
        "baths": listing.get("baths"),
        "sqft": sqft,
        "price_per_sqft": round(listing["price"] / sqft) if sqft else None,
        "lot_acres": listing.get("lot_acres"),
        "year_built": listing.get("year_built"),
        "garage": listing.get("garage"),
        "hoa_monthly": listing.get("hoa_monthly") or 0,
        "days_on_market": listing.get("days_on_market"),
        "url": listing.get("url"),
        "photo": listing.get("photo"),
        "photo_count": listing.get("photo_count"),
        "source": listing.get("source"),
        "stretch": listing["price"] > c["budget"]["target_max"],
        "fit_score": score,
        "score_breakdown": breakdown,
        "pros": pros,
        "cons": cons,
        "rehab_tier": flip["tier"],
        "market_value": flip.get("market_value"),
        "arv": flip.get("arv"),
        "rehab_cost": flip.get("rehab"),
        "selling_costs": flip.get("selling_costs"),
        "buy_hold_costs": flip.get("buy_hold_costs"),
        "flip_profit": flip.get("net_profit"),
        "flip_roi_pct": flip.get("roi_pct"),
        "live_in_years": live["years"],
        "live_in_value": live["future_value"],
        "live_in_gain": live["equity_gain"],
        "live_in_roi_pct": round(100 * live["equity_gain"] / aff["cash_needed"]) if aff["cash_needed"] else None,
        "max_offer": live["max_offer"],
        "buy_closing": round(listing["price"] * c["buyer"]["closing_cost_pct"]),
        "sale_costs": round(live["future_value"] * c["flip"]["selling_cost_pct"]),
        "target_profit": live["target_profit"],
        "monthly_payment": aff["monthly"],
        "payment_level": aff["payment_level"],
        "cash_needed": aff["cash_needed"],
        "cash_ok": aff["cash_ok"],
    }


def merge_history(db, raw, today, c):
    """Update the history db in place with today's raw scan. Returns rejected list."""
    seen_today = set()
    rejected = []
    listings = db.setdefault("listings", {})
    for item in raw:
        ok, reason = passes_filters(item, c)
        lid = listing_id(item)
        if not ok:
            rejected.append({"id": lid, "reason": reason})
            continue
        seen_today.add(lid)
        rec = listings.get(lid)
        if rec is None:
            rec = {"first_seen": today, "price_history": []}
            listings[lid] = rec
        hist = rec["price_history"]
        if not hist or hist[-1]["price"] != item["price"]:
            hist.append({"date": today, "price": item["price"]})
        rec["last_seen"] = today
        rec["status"] = (item.get("status") or "active").lower()
        rec["data"] = item
    for lid, rec in listings.items():
        if lid not in seen_today and rec.get("status") != "gone":
            last = dt.date.fromisoformat(rec["last_seen"])
            if (dt.date.fromisoformat(today) - last).days >= 3:
                rec["status"] = "gone"
    db["updated"] = today
    return rejected


def dashboard_rows(db, c):
    rows = []
    for lid, rec in db.get("listings", {}).items():
        if rec.get("status") == "gone":
            continue
        item = dict(rec["data"])
        hist = rec["price_history"]
        if len(hist) > 1 and hist[0]["price"] > hist[-1]["price"]:
            item["price_drop"] = hist[0]["price"] - hist[-1]["price"]
        row = score_listing(item, c)
        row["status"] = rec["status"]
        row["first_seen"] = rec["first_seen"]
        row["last_seen"] = rec["last_seen"]
        row["price_history"] = hist
        row["price_drop"] = item.get("price_drop", 0)
        rows.append(row)
    rows.sort(key=lambda r: -r["fit_score"])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--db", default=str(ROOT / "data" / "listings.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "dashboard.json"))
    ap.add_argument("--criteria", default=str(DEFAULT_CRITERIA))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    args = ap.parse_args()

    c = load_criteria(args.criteria)
    raw = json.loads(Path(args.raw).read_text())
    db_path = Path(args.db)
    db = json.loads(db_path.read_text()) if db_path.exists() else {"listings": {}}
    rejected = merge_history(db, raw, args.today, c)
    rows = dashboard_rows(db, c)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_text(json.dumps(db, indent=1, sort_keys=True))
    by = c["buyer"]
    plan = {k: by[k] for k in ("down_payment_pct", "closing_cost_pct", "mortgage_rate", "mortgage_rate_as_of")}
    plan["comfortable_price"] = comfortable_price(c)
    plan.update({"hold_years": c["live_in"]["years"], "appreciation": c["live_in"]["annual_appreciation"],
                 "target_profit": c["live_in"]["target_profit"], "selling_cost_pct": c["flip"]["selling_cost_pct"],
                 "rehab_per_sqft": c["flip"]["rehab_per_sqft"], "target_max": c["budget"]["target_max"]})
    Path(args.out).write_text(json.dumps({"updated": args.today, "plan": plan, "rows": rows}, indent=1))
    print(f"kept {len(raw) - len(rejected)} of {len(raw)} scanned; {len(rows)} on dashboard")
    for r in rejected:
        print(f"  rejected {r['id']}: {r['reason']}")


if __name__ == "__main__":
    main()
