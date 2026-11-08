#!/usr/bin/env python3
"""Compare two versions of docs/dashboard.json and write an alert for strong new homes and price cuts.

Usage: python3 scripts/alerts.py --before old.json --after docs/dashboard.json --out alert.md

Writes nothing (and no file) when there is nothing worth alerting on. Otherwise the first line of
--out is the issue title and the rest is the body (GitHub markdown).
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://hdruryut.github.io/Utah-Home/"


def k(n):
    return "—" if n is None else ("−$" if n < 0 else "$") + f"{abs(n) / 1000:.0f}k"


def find_alerts(before, after, min_score):
    prev = {r["id"]: r for r in before.get("rows", [])}
    new, cuts = [], []
    for r in after.get("rows", []):
        if r.get("status") != "active" or r.get("fit_score", 0) < min_score:
            continue
        old = prev.get(r["id"])
        if old is None or old.get("status") != "active":
            new.append(r)
        elif r["price"] < old["price"]:
            cuts.append((r, old["price"] - r["price"]))
    new.sort(key=lambda r: -r["fit_score"])
    cuts.sort(key=lambda x: -x[0]["fit_score"])
    return new, cuts


def block(r, extra=""):
    lines = [f"### [{r['address']}, {r['city']}]({SITE}#{r['id']}) · fit {r['fit_score']}{extra}"]
    if r.get("photo"):
        lines.append(f'<img src="{r["photo"]}" width="420" alt="Photo of {r["address"]}">')
        lines.append("")
    facts = " · ".join(x for x in [
        f"**{k(r['price'])}**",
        f"{r['beds']} bd" if r.get("beds") else None,
        f"{r['baths']:g} ba" if r.get("baths") else None,
        f"{r['sqft']:,} sqft" if r.get("sqft") else None,
        f"{r['lot_acres']:.2f} ac" if r.get("lot_acres") else None,
        f"built {r['year_built']}" if r.get("year_built") else None,
    ] if x)
    lines.append(facts)
    yrs = r.get("live_in_years", 3)
    money = [f"{yrs}-yr profit at asking: **{k(r.get('live_in_gain'))}**",
             f"payment ~${r['monthly_payment']:,}/mo ({r.get('payment_level', '?')})" if r.get("monthly_payment") else None]
    if r.get("max_offer") is not None and r["max_offer"] < r["price"]:
        money.append(f"offer ≤ {k(r['max_offer'])} to clear {k(r.get('target_profit'))}")
    lines.append(" · ".join(x for x in money if x))
    if r.get("pros"):
        lines.append("**Pros:** " + "; ".join(r["pros"][:4]))
    if r.get("cons"):
        lines.append("**Cons:** " + "; ".join(r["cons"][:4]))
    links = [f"[Details and photos]({SITE}#{r['id']})"]
    if r.get("url"):
        links.append(f"[Listing]({r['url']})")
    lines.append(" · ".join(links))
    return "\n".join(lines)


def build(new, cuts, updated, notify):
    parts = []
    if new:
        parts.append(f"{len(new)} new home{'s' if len(new) != 1 else ''}")
    if cuts:
        parts.append(f"{len(cuts)} price cut{'s' if len(cuts) != 1 else ''}")
    title = f"{' and '.join(parts)} worth a look: {updated}"
    body = []
    if notify:
        body.append(f"@{notify} the morning scan found something.\n")
    if new:
        body.append("## New listings\n")
        body += [block(r) + "\n" for r in new]
    if cuts:
        body.append("## Price cuts\n")
        body += [block(r, f" · price cut {k(d)}") + "\n" for r, d in cuts]
    body.append(f"---\n[Open the dashboard]({SITE}). Estimates only; confirm on the listing before acting.")
    return title + "\n" + "\n".join(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    crit = json.loads((ROOT / "criteria.json").read_text())
    cfg = crit.get("alerts", {})
    try:
        before = json.loads(Path(args.before).read_text() or "{}")
    except (FileNotFoundError, json.JSONDecodeError):
        before = {}
    after = json.loads(Path(args.after).read_text())
    if not before.get("rows"):
        print("No previous scan to compare with; skipping alerts.")
        return
    new, cuts = find_alerts(before, after, cfg.get("min_fit_score", 50))
    if not new and not cuts:
        print("Nothing new worth alerting on.")
        return
    Path(args.out).write_text(build(new, cuts, after.get("updated", ""), cfg.get("notify")))
    print(f"Alert written: {len(new)} new, {len(cuts)} price cuts.")


if __name__ == "__main__":
    main()
