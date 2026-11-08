# Daily scan — instructions for the scheduled Claude routine

Goal: find every **detached single-family home** for sale in **Utah County, UT** at or under **$395,000** with **2+ bedrooms**. Score them and publish them to the GitHub Pages site, which serves `docs/index.html` and reads `docs/dashboard.json`.

Today's date in America/Denver is `TODAY` (YYYY-MM-DD) below.

## 1. Collect listings

Use WebFetch (not curl, and never spoof a browser user agent) on Century21's per-city single-family pages:

```
https://www.century21.com/homes/list/city/ut/<city-slug>/single-family-homes
```

City slugs: provo, orem, spanish-fork, springville, payson, santaquin, salem, mapleton, goshen,
american-fork, pleasant-grove, lehi, saratoga-springs, eagle-mountain, lindon, vineyard,
elk-ridge, genola, woodland-hills, cedar-hills, highland, alpine.

Prompt for each page: *"Return ONLY listings priced at or below $395,000 as a table: address, city,
price, beds, baths, sqft, status, URL path. Report how many listings were visible vs the total stated."*

- Big cities (Provo, Orem, Lehi, Saratoga Springs, Eagle Mountain, Spanish Fork, Salem) get truncated.
  Fetch them again with `offset` 30000 and 60000 to read further.
- In the same call, ask for each card's photo: *"…, URL path, exact photo img src URL"*. Photo URLs look like
  `https://images-listings.century21.com/SL/21/92/98/2/_P/2192982_P00.jpg?format=webp&quality=70&width=600`.
  Swap `width=600` for `width=800`. If a fetch refuses to give image URLs, build one from the MLS number:
  `SL/<m[0:2]>/<m[2:4]>/<m[4:6]>/<m[6]>/_P/<m>_P00.jpg`. Usually that matches, and the site hides broken photos.
- Cross-check other sources. Zillow, Homes.com, Redfin, Realtor.com, UtahRealEstate.com and KSL Classifieds
  block direct fetches, so use WebSearch with `allowed_domains`:
  - `["zillow.com"]`: `Utah County UT single family house for sale under $380,000`
  - `["homes.com"]`: `Utah County UT house for sale under $380k`
  - `["utahrealestate.com","ksl.com","bestutahrealestate.com"]`: `<city> UT single family home for sale under $380,000`
    (KSL also carries for-sale-by-owner homes, which never appear on Century21)
  Search snippets are often months old. Only add an address after confirming it is **currently active** on a page you
  can fetch: the Century21 detail page (search `"<address>" <city> UT century21`), Coldwell Banker, BHGRE,
  or a KSL listing page. Set `source` to where you confirmed it.
- Completeness check: `https://bestutahrealestate.com/search-results?county[]=Utah&maxPrice=395000&type=Single+Family`
  reports a total count. If you found far fewer homes than that total, keep searching.

## 2. Get details for each active candidate

Only fetch details for listings that are **new** (not in `data/listings.json`) or whose **price changed**. For the others, copy their data forward from `data/listings.json` and update only price and status.
For each listing you do fetch, open the detail URL with the prompt:
*"Return JSON only: {address, city, price, status, beds, baths, sqft, lot_acres, year_built,
property_type (exact text), style, garage (present, spaces), hoa_monthly, days_on_market, mls_number,
description (first 400 chars, paraphrase ok)}."*

Write one object per listing to `data/raw/TODAY.json`. Use this shape (see `data/raw/2026-11-07.json`):

```json
{"address":"314 N 100 W","city":"Payson","price":379500,"status":"active|pending",
 "beds":3,"baths":2,"sqft":1546,"lot_acres":0.27,"year_built":1913,
 "property_type":"single_family","garage":false,"hoa_monthly":0,"days_on_market":59,
 "mls":"2183978","source":"Century21","url":"https://...","photo":"https://images-listings.century21.com/...",
 "description":"..."}
```

Rules:
- `property_type` must be `single_family` only when the listing says single family. Keep the
  description verbatim enough that words like "twin home", "townhome", "cash offers only",
  "short sale", "as-is" and "backup offers" survive. The scorer uses those words to reject listings or flag them.
- "Accepting backup offers", "under contract" and "pending" all mean `"status": "pending"`.
- Include pending listings, which can be summary data only. Leave sold listings out.
- Never invent a field. Leave it out if the page doesn't show it.

## 3. Score

```
python3 scripts/score.py --raw data/raw/TODAY.json --today TODAY
python3 -m unittest scripts/test_score.py
```

This updates `data/listings.json` (history) and writes `docs/dashboard.json`, the file the site reads. Listings missing from the
scan for 3+ days are marked gone automatically.

Monthly (first scan of the month): refresh `city_market` in `criteria.json`. Use the
"Median price per sq ft (closed sales)" and "Typical size" from
`https://bestutahrealestate.com/utah/<city-slug>/homes-under-500k` for each city, set
`"source": "measured"`, and update `city_market_last_refreshed`.

## 4. Publish

Publishing is the commit below. GitHub Pages serves `docs/` and refreshes a minute or two after the push.

## 5. Commit

```
git add data/ docs/dashboard.json criteria.json
git commit -m "Daily scan TODAY: <N> active, <M> new"
git push
```

## 6. Summary

End with a short summary: how many homes are active, which are new today (address, price, fit score),
any price drops, and any listings that went pending or disappeared.
