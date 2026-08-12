#!/usr/bin/env python3
"""
Photo enrichment for entry cards, cached in config/images.json (persists on the
Render disk; survives core re-syncs, unlike chroma metadata which import_eatlist
rebuilds). Three layered sources, cheapest first:

  1. og:image  — for entries that link to a real website (free, polite fetch).
  2. Instagram media — captured by ig_cloud for API-pulled posts (free).
  3. Google Places photo — broad coverage, DORMANT unless GOOGLE_MAPS_API_KEY is
     set (billed per lookup). The Places photo endpoint 302-redirects to a
     keyless googleusercontent URL, which is what we cache — so the API key is
     never embedded in an <img src> served to the browser.

`get(meta)` returns a cached image URL (or "") with no network call. `warm(n)`
fills the cache for entries that still have no photo.
"""
import argparse
import hashlib
import json
import os
import re
import time

import requests

import ingest

CACHE = os.path.join("config", "images.json")
UA = getattr(ingest, "USER_AGENT", "Makanapa/1.0 (+personal food guide)")
_OG_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                    re.I)


def _key(m) -> str:
    return hashlib.sha1((f"{m.get('title', '')}|{m.get('city', '')}").encode()).hexdigest()


def _load() -> dict:
    try:
        return json.load(open(CACHE))
    except (ValueError, OSError):
        return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(d, open(CACHE, "w"))


def get(m) -> str:
    """Cached photo URL for an entry, or '' (no network)."""
    return _load().get(_key(m), "")


# ── sources ──────────────────────────────────────────────────────────────────
def og_image(url: str) -> str:
    if not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
        if r.status_code == 200:
            mt = _OG_RE.search(r.text)
            return mt.group(1) if mt else ""
    except requests.RequestException:
        pass
    return ""


def places_photo(name: str, address: str, key: str) -> str:
    """Google Places photo → a keyless googleusercontent URL (or '')."""
    if not key:
        return ""
    try:
        q = ", ".join(p for p in (name, address) if p and p != "-")
        f = requests.get(
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            params={"input": q, "inputtype": "textquery",
                    "fields": "place_id", "key": key}, timeout=12).json()
        cands = f.get("candidates") or []
        if not cands:
            return ""
        det = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={"place_id": cands[0]["place_id"], "fields": "photo",
                    "key": key}, timeout=12).json()
        photos = (det.get("result") or {}).get("photos") or []
        if not photos:
            return ""
        # the photo endpoint 302s to a keyless lh3.googleusercontent URL — cache that
        r = requests.get("https://maps.googleapis.com/maps/api/place/photo",
                         params={"maxwidth": 800, "key": key,
                                 "photo_reference": photos[0]["photo_reference"]},
                         allow_redirects=False, timeout=12)
        return r.headers.get("Location", "")
    except (requests.RequestException, ValueError, KeyError):
        return ""


def enrich_one(m, key=None) -> str:
    """Best available photo URL for one entry (og:image → Google Places)."""
    url = m.get("url", "")
    img = og_image(url) if url.startswith("http") else ""
    if not img:
        img = places_photo(m.get("title", ""),
                           m.get("address", "") or m.get("city", ""),
                           key or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip())
    return img


def warm(limit: int) -> int:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    metas = ingest.get_collection().get(include=["metadatas"]).get("metadatas", []) or []
    cache = _load()
    done = 0
    for m in metas:
        if done >= limit:
            break
        k = _key(m)
        if k in cache or (m.get("image") or "").strip():
            continue
        if not str(m.get("url", "")).startswith("http") and not key:
            continue                              # nothing to try without a URL or key
        img = enrich_one(m, key)
        cache[k] = img                            # cache even '' to avoid re-hitting
        done += 1
        if done % 15 == 0:
            _save(cache)
            print(f"  {done} processed…")
        time.sleep(0.3)
    _save(cache)
    hits = sum(1 for v in cache.values() if v)
    print(f"Processed {done}; cache now holds {hits} photos → {CACHE}")
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Warm the entry-photo cache.")
    ap.add_argument("--warm", type=int, default=200)
    args = ap.parse_args()
    warm(args.warm)
