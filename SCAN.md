# Daily scan — instructions for the scheduled Claude routine

Goal: find every **detached single-family home** for sale in **Utah County, UT** at or under **$395,000** with **2+ bedrooms**. Score them and publish them to the GitHub Pages site, which serves `docs/index.html` and reads `docs/dashboard.json`.

Today's date in America/Denver is `TODAY` (YYYY-MM-DD) below.

## Every run does every step

Steps 1a, 1b, 1c, 1d (the Zillow, Homes.com and KSL searches), 2, 3 and 5 are **all required on every run**, even if 1a finds nothing new, and even if
today's raw file already exists (merge into it). The rolling sweep (1b) and re-checks (1c) find things 1a can't:
older listings, price cuts, and homes that sold. Don't stop early. Read both halves of every page (normal plus
`offset: 100000`). Always commit and push the updated `data/sweep.json`, even when no listings changed.

The summary must list: pages read in 1a, the page range swept in 1b, how many listings 1c re-checked, and which
sources were blocked.

## 1. Collect listings

Use WebFetch for all web access. Never use curl with a spoofed browser user agent, and never try to get around a
site that blocks you. Century21 returns HTTP 403 to the cloud scan, so **Coldwell Banker is the main source**. It
carries every Utah County MLS listing and works from the cloud.

### 1a. New listings: Coldwell Banker county pages, newest first (every day)

```
https://www.coldwellbankerhomes.com/ut/utah-county/            (page 1)
https://www.coldwellbankerhomes.com/ut/utah-county/p_<n>/      (page n)
```

About 4,000 listings, 20 per page, **newest first**. Each listing's URL ends in `pid_<number>`, and pid numbers only
go up, so a higher pid means a newer listing. Each page's text runs past 100,000 characters, so read every page
**twice**: once normally and once with `offset: 100000`.

Prompt, phrased as a question. WebFetch's reader sometimes mistakes an imperative prompt for text injected into the
page and refuses it: *"Which listings on this page are Single Family homes priced at $450,000 or less? For each one,
what are its address, city, price, beds, baths, sqft, status and url path? Also, what are the lowest and highest
pid_ numbers among all listings shown (any type or price), and what is the highest page number linked?"* If a reply
refuses or answers something else, ask again once with the same question.

Start at page 1 and keep going until a page's **highest** pid is at or below `max_pid_seen` in `data/sweep.json`
(nothing newer is left). Read at least 3 pages, even if page 1 has nothing new. Then set `max_pid_seen` to the highest pid you saw.

- Homes **at or under $395,000** are candidates: get their details (step 2).
- Homes **$395,001–$450,000** go on the watch list in `data/watch.json` as
  `{"<url path>": {"address", "city", "price", "first_seen"}}`. Price cuts often bring these into range. Step 1b
  rechecks them.

### 1b. Rolling sweep: older listings and price cuts (every day)

Also read **30 more pages**, starting at `rolling_page` in `data/sweep.json`. Use the same prompt, both halves of each
page. Treat what you find the same way: candidates at or under $395k, watch list for $395k–$450k. Then advance
`rolling_page` by 30, and wrap back to 1 once you pass the highest page number. That covers the whole county about
once a week and catches older listings and price cuts into range.

Once a week (Mondays), open the detail page of every watch-list home and record its current price and status.
Homes cut to $395k or less become candidates. Remove sold or off-market homes from the watch list.

### 1c. Re-check known listings

For each listing that is `active` or `pending` in `data/listings.json` but wasn't seen in 1a or 1b today **and**
hasn't been seen for 3+ days, open its page and check that it's still for sale. Prefer its Coldwell Banker URL
(`cb_url` field); if it has none, find one with WebSearch: `"<address>" <city> UT coldwellbankerhomes`.
- Still for sale: add it to today's raw file with the current price and status, plus `cb_url`.
- Sold, off market, or the page is gone: add `{"address", "city", "status": "gone"}`, which drops it right away.

The scorer keeps a listing up to `scan.gone_after_days` (10) days without a sighting, so a home isn't dropped just
because a partial scan didn't reach it.

### 1c-alt. Century21 zip pages (only if they load)

If `https://www.century21.com/homes/list/zip/ut/84606/single-family-homes` returns a page instead of 403, also read
the Century21 zip pages for 84601, 84604, 84606, 84057, 84058, 84097, 84059, 84042, 84062, 84004, 84013, 84651,
84655, 84633, 84626, 84663 and 84664. They're a quick completeness check. Prompt: *"First line: the results header
exactly and whether the content was truncated. Then ONLY listings priced at or below $395,000 as lines: "address,
city | price | beds | baths | sqft | status | url path | photo img src". If none, say NONE."* If they 403, skip them.

### 1d. Cross-check other sources (required every run)

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
For each listing you do fetch, open its Coldwell Banker detail page (`https://www.coldwellbankerhomes.com<url path>`)
and store that URL as both `url` and `cb_url`, with `"source": "Coldwell Banker"`. Use the prompt:
*"Return JSON only: {address, city, price, status, beds, baths, sqft, lot_acres, year_built,
property_type (exact text), style, garage (present, spaces), hoa_monthly, days_on_market, mls_number,
description (first 400 chars, paraphrase ok)}."*

**Photo:** Coldwell Banker's page doesn't expose usable photo links, but the MLS number gives the Century21 photo
URL, which viewers' browsers can load:
`https://images-listings.century21.com/SL/<m[0:2]>/<m[2:4]>/<m[4:6]>/<m[6]>/_P/<m>_P00.jpg?format=webp&quality=70&width=800`
(m = the 7-digit MLS number). The site hides a photo that doesn't load.

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
