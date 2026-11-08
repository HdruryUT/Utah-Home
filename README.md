# Utah County starter home finder

Finds detached single-family homes in Utah County at or under ~$380k (with a $395k stretch), scores them, and
publishes them to a shared dashboard:

**Dashboard:** https://hdruryut.github.io/Utah-Home/ (GitHub Pages, served from `docs/`)

## How it works

| Piece | What it does |
|---|---|
| `SCAN.md` | Instructions the daily Claude routine follows: collect, score, publish, commit |
| `criteria.json` | Every rule and number: budget, filters, score weights, rehab costs, city $/sqft |
| `scripts/score.py` | Filters, fit score, pros/cons, flip and fix-and-live estimates, listing history |
| `scripts/test_score.py` | `python3 -m unittest scripts/test_score.py` |
| `data/raw/<date>.json` | What each day's scan found |
| `data/listings.json` | History of every listing: first/last seen, status, price changes |
| `docs/index.html` | The dashboard page (GitHub Pages) |
| `docs/dashboard.json` | Scored rows the page reads, rewritten by each scan |

## Changing what you're looking for

Edit `criteria.json`, for example `budget.target_max`, `requirements.min_beds` or `weights`. Then re-score:

```
python3 scripts/score.py --raw data/raw/<latest>.json --today <latest>
```

Then commit and push. The site updates within a couple of minutes.

## The estimates are rough

The ARV, rehab, flip and 5-year numbers are formula estimates for comparing homes. They are not appraisals.
The formulas are listed at the bottom of the dashboard.
