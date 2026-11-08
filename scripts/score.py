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
    """Fix it up, live there N years, then sell: gain after rehab and selling costs."""
    li = c["live_in"]
    price = listing["price"]
    base = flip.get("arv") or price
    rehab = flip.get("rehab") or 0
    future = base * (1 + li["annual_appreciation"]) ** li["years"]
    gain = future - price - rehab - future * c["flip"]["selling_cost_pct"]
    return {"years": li["years"], "future_value": round(future), "equity_gain": round(gain)}


def fit_score(listing, flip, c):
    w = c["weights"]
    b = c["budget"]
    price = listing["price"]
    pts = {}
    pts["price"] = w["price"] * clamp((b["stretch_max"] - price) / (b["stretch_max"] - b["price_floor_for_scoring"]))

    sqft = listing.get("sqft")
    if sqft:
        ratio = (price / sqft) / flip["median_ppsf"]
        pts["value_vs_market"] = w["value_vs_market"] * clamp((1.2 - ratio) / 0.5)
    else:
        pts["value_vs_market"] = w["value_vs_market"] * 0.3

    beds = listing.get("beds") or 0
    baths = listing.get("baths") or 0
    bb = {2: 0.45, 3: 0.75}.get(beds, 0.85 if beds >= 4 else 0)
    bb += 0.15 if baths >= 2 else (0.08 if baths >= 1.5 else 0)
    pts["beds_baths"] = w["beds_baths"] * clamp(bb)

    lot = listing.get("lot_acres") or 0
    lg = (0.5 if listing.get("garage") else 0) + (0.5 if lot >= 0.2 else 0.3 if lot >= 0.12 else 0)
    pts["lot_garage"] = w["lot_garage"] * lg

    pts["condition"] = w["condition"] * {"light": 1.0, "medium": 0.6, "heavy": 0.3}[flip["tier"]]

    profit = flip.get("net_profit") or 0
    live_gain = live_in_estimate(listing, flip, c)["equity_gain"]
    pts["flip_upside"] = w["flip_upside"] * max(
        clamp(profit / c["flip"]["full_points_profit"]),
        clamp(live_gain / c["flip"]["full_points_live_in_gain"]))

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
    if under >= 20000:
        pros.append(f"{money(under)} under your $380k budget")
    elif under >= 0:
        pros.append("Within budget")
    else:
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
    if beds >= 3:
        pros.append(f"{beds} bedrooms: room for an office or guests, and better resale")
    if baths and baths < 1.5:
        cons.append("Only 1 bathroom")
    elif baths >= 2:
        pros.append(f"{baths:g} bathrooms")

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
        cons.append("Listed as needing work: budget for a thorough inspection")
    elif tier == "light" and (not yb or yb < 2005):
        pros.append("Updates mentioned in the listing")

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

    profit = flip.get("net_profit")
    if profit is not None:
        if profit >= 40000:
            pros.append(f"Strong flip upside: ~{money(profit)} estimated net")
        elif profit < 0:
            cons.append("A quick flip would likely lose money after fees; it works better as a live-in")
    live_gain = live_in_estimate(listing, flip, c)["equity_gain"]
    if live_gain >= 80000:
        pros.append(f"Fix up and live {c['live_in']['years']} yrs: ~{money(live_gain)} estimated gain")
    return pros, cons


def score_listing(listing, c):
    flip = flip_estimate(listing, c)
    score, breakdown = fit_score(listing, flip, c)
    pros, cons = pros_cons(listing, flip, c)
    live = live_in_estimate(listing, flip, c)
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
    Path(args.out).write_text(json.dumps({"updated": args.today, "rows": rows}, indent=1))
    print(f"kept {len(raw) - len(rejected)} of {len(raw)} scanned; {len(rows)} on dashboard")
    for r in rejected:
        print(f"  rejected {r['id']}: {r['reason']}")


if __name__ == "__main__":
    main()
