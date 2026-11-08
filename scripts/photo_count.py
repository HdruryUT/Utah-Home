#!/usr/bin/env python3
"""Fill in `photo_count` for listings in a raw scan file.

Century21 photos are numbered ..._P00.jpg, _P01.jpg, ... This checks which numbers exist
(plain HEAD requests to the image server, a few per listing) and writes the count back.

Usage: python3 scripts/photo_count.py data/raw/2026-11-07.json [--force]
"""
import json
import re
import sys
import urllib.error
import urllib.request

MAX = 60


def exists(url):
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def url_for(photo, n):
    return re.sub(r"_P\d{2}\.jpg", f"_P{n:02d}.jpg", photo)


def count_photos(photo):
    if not photo or not re.search(r"_P\d{2}\.jpg", photo) or not exists(url_for(photo, 0)):
        return 0
    lo, hi = 0, 1  # lo exists; find a hi that doesn't
    while hi < MAX and exists(url_for(photo, hi)):
        lo, hi = hi, min(hi * 2, MAX)
    while hi - lo > 1:  # binary search for the last existing number
        mid = (lo + hi) // 2
        if exists(url_for(photo, mid)):
            lo = mid
        else:
            hi = mid
    return lo + 1


def main():
    path = sys.argv[1]
    force = "--force" in sys.argv
    rows = json.load(open(path))
    for r in rows:
        if r.get("photo") and (force or not r.get("photo_count")):
            r["photo_count"] = count_photos(r["photo"])
            print(f"{r['address']}, {r['city']}: {r['photo_count']} photos")
    json.dump(rows, open(path, "w"), indent=1)


if __name__ == "__main__":
    main()
