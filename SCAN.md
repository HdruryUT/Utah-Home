# Daily scan — instructions for the scheduled Claude routine

Goal: find every **detached single-family home** for sale in **Utah County, UT** at or under **$395,000** with **2+ bedrooms**. Score them and publish them to the GitHub Pages site, which serves `docs/index.html` and reads `docs/dashboard.json`.

Today's date in America/Denver is `TODAY` (YYYY-MM-DD) below.

## 1. Collect listings

Use WebFetch for all web access (never curl with a spoofed browser user agent). City-level pages miss homes, because
Century21 renders only about 150 cards per page and its city pages leave out part of each zip. So scan **by zip code**.

### 1a. Century21 zip pages (every zip, every day)

```
https://www.century21.com/homes/list/zip/ut/<zip>/single-family-homes
```

Prompt: *"First line: the results header exactly (e.g. "46 of 46 results displayed") and whether the content was
truncated. Then ONLY listings priced at or below $395,000 as lines: "address, city | price | beds | baths | sqft |
status | url path | photo img src". If none, say NONE."*

| Zip | Area | Notes |
|---|---|---|
| 84601, 84604, 84606 | Provo | |
| 84057, 84058, 84097 | Orem | |
| 84059 | Vineyard | |
| 84042 | Lindon | |
| 84062 | Pleasant Grove, Cedar Hills | |
| 84004 | Alpine | |
| 84013 | Cedar Fort, Fairfield, west Eagle Mountain | |
| 84651 | Payson, Elk Ridge | |
| 84655 | Santaquin, Genola | |
| 84633 | Goshen | |
| 84626 | Elberta | |
| 84663 | Springville | |
| 84664 | Mapleton | |
| 84660 | Spanish Fork | **big**: also do 1b |
| 84653 | Salem, Woodland Hills | **big**: also do 1b |
| 84003 | American Fork, Highland | **big**: also do 1b |
| 84043 | Lehi | **big**: also do 1b |
| 84045 | Saratoga Springs | **big**: also do 1b |
| 84005 | Eagle Mountain | **big**: also do 1b |

If a zip not marked big reports a total over ~140, or says it was truncated, treat it as big today and do 1b for it too.

Prefer photo URLs from the page. They look like
`https://images-listings.century21.com/SL/21/92/98/2/_P/2192982_P00.jpg?format=webp&quality=70&width=600`;
store them with `width=800`.

### 1b. Coldwell Banker pages for big zips

Coldwell Banker shows **every** listing (all property types) 20 per page, **newest first**:

```
https://www.coldwellbankerhomes.com/ut/<city-slug>/            (page 1)
https://www.coldwellbankerhomes.com/ut/<city-slug>/p_<n>/      (page n)
```

City slugs: spanish-fork, salem, woodland-hills, american-fork, highland, lehi, saratoga-springs, eagle-mountain.
Each page's text runs past 100,000 characters, so read every page **twice**: once normally and once with
`offset: 100000`. Prompt: *"Return ONLY listings priced at or below $395,000 (any type) as lines: "address | price |
beds | baths | sqft | type | status | url path". If none, say NONE. Also give the total results count and the
highest page number linked."* Keep only `Single Family` rows.

- **Full sweep** (every page) when `data/sweep.json`'s `last_full_sweep` is missing or 7+ days old. Then set it to TODAY.
- **Otherwise**, read pages 1–3 only. That catches every new listing, since pages are newest first.

Coldwell Banker detail pages (`https://www.coldwellbankerhomes.com/<url path>`) give the same fields as Century21
detail pages. Use them for homes found here. For the photo, search `"<address>" <city> UT century21` to find the
Century21 detail page and take its photo URL. If there's none, leave `photo` out.

### 1c. Re-check known listings you didn't see today

For every listing that is `active` or `pending` in `data/listings.json` but did not appear in today's 1a/1b results,
open its stored `url` (or its Coldwell Banker page). If it is still active or pending, include it in today's raw file
with the current price and status. If it says sold, off market, or the page is gone, leave it out. The scorer marks
it gone after 3 days. Without this step, homes deeper in the big-zip pages would wrongly drop off the dashboard.

### 1d. Cross-check other sources

Zillow, Homes.com, Redfin, Realtor.com, UtahRealEstate.com and KSL Classifieds block direct fetches, so use
WebSearch with `allowed_domains`:
- `["zillow.com"]`: `Utah County UT single family house for sale under $380,000`
- `["homes.com"]`: `Utah County UT house for sale under $380k`
- `["utahrealestate.com","ksl.com","bestutahrealestate.com"]`: `<city> UT single family home for sale under $380,000`
  (KSL also carries for-sale-by-owner homes, which never appear on Century21 or Coldwell Banker)

Search snippets are often months old. Only add an address after confirming it is **currently active** on a page you
can fetch: Century21, Coldwell Banker, BHGRE, or a KSL listing page. Set `source` to where you confirmed it.

**Completeness check:** `https://bestutahrealestate.com/search-results?county[]=Utah&maxPrice=395000&type=Single+Family`
reports a total count. If you found far fewer single-family homes than that total, say so in the summary.

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

Then count each listing's photos (the site's photo gallery uses this; it only checks listings missing a count):

```
python3 scripts/photo_count.py data/raw/TODAY.json
```

Copy `photo_count` forward from `data/listings.json` for unchanged listings, as with every other field.

## 3. Score

Weekly (first scan on or after each Thursday): update `buyer.mortgage_rate` and `buyer.mortgage_rate_as_of`
in `criteria.json` with the latest Freddie Mac 30-year fixed average (WebSearch: `Freddie Mac 30-year mortgage rate this week`).

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
