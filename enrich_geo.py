#!/usr/bin/env python3
"""
Geo-enrichment for FoodRAG — give chunks lat/lng so query.py can do "nearby".

Two sources, cheapest first:
  1. **Explicit coordinates** already in the text (many ChiefEater / KY Speaks
     place pages print `GPS: 2.1984, 102.2513`, a maps link, or "Coordinates:").
     Free, exact, no API.
  2. **Geocoded addresses** — for pages with an `Address:` line but no GPS, and
     only if `GOOGLE_PLACES_API_KEY` is set, we geocode the first address via
     Google's Geocoding API.

Coordinates are written to every chunk of the article as metadata `lat`, `lng`,
`geo_source`. Re-running is safe (idempotent update). Nothing here fetches the
web except the optional geocode call.

    python enrich_geo.py               # GPS extraction only (no key needed)
    python enrich_geo.py --places      # also geocode addresses (needs API key)
    python enrich_geo.py --overwrite   # re-derive even chunks already tagged
"""
import argparse
import os
import re
from collections import defaultdict

import ingest

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Labeled coordinate patterns (high precision — we avoid bare "1.23, 103.8"
# number pairs, which collide with prices/phone numbers).
GPS_PATTERNS = [
    re.compile(r"GPS[:\s]+(-?\d{1,2}\.\d{3,})[,\s]+(-?\d{2,3}\.\d{3,})", re.I),
    re.compile(r"(?:coordinates?|lat(?:itude)?)[:\s]+(-?\d{1,2}\.\d{3,})[,\s]+"
               r"(-?\d{2,3}\.\d{3,})", re.I),
    re.compile(r"(?:google\.[^\s/]+/maps|goo\.gl/maps|maps\.app\.goo\.gl)"
               r"[^\s]*?@?(-?\d{1,2}\.\d{3,}),(-?\d{2,3}\.\d{3,})", re.I),
]
ADDRESS_RE = re.compile(r"Address[:\s]+(.{6,160}?)"
                        r"(?:\s{2,}|Tel[:\s]|GPS[:\s]|Open|Hour|Business|$)", re.I)


def extract_latlng(text: str):
    for p in GPS_PATTERNS:
        m = p.search(text)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return lat, lng
    return None


def extract_address(text: str):
    m = ADDRESS_RE.search(text)
    return m.group(1).strip() if m else None


def geocode_google(address: str, key: str):
    import requests
    try:
        r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                         params={"address": address, "key": key}, timeout=15)
        j = r.json()
        if j.get("status") == "OK" and j.get("results"):
            loc = j["results"][0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    except Exception as e:
        print(f"    geocode failed for {address[:40]!r}: {e}")
    return None


def run(use_places: bool, overwrite: bool):
    coll = ingest.get_collection()
    got = coll.get(include=["metadatas", "documents"])
    ids, metas, docs = got["ids"], got["metadatas"], got["documents"]

    idx_by_url = defaultdict(list)
    for i, m in enumerate(metas):
        idx_by_url[m.get("url", "")].append(i)

    key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if use_places and not key:
        print("--places set but GOOGLE_PLACES_API_KEY is not configured — "
              "doing GPS extraction only.")
        use_places = False

    n_gps = n_geo = n_skip = n_none = 0
    upd_ids, upd_meta = [], []

    for url, idxs in idx_by_url.items():
        if not url:
            continue
        if not overwrite and metas[idxs[0]].get("lat") is not None:
            n_skip += 1
            continue

        full = " ".join(docs[i] for i in idxs)
        lat = lng = None
        geo_source = None

        ll = extract_latlng(full)
        if ll:
            lat, lng = ll
            geo_source = "gps"
            n_gps += 1
        elif use_places:
            addr = extract_address(full)
            if addr:
                gc = geocode_google(addr, key)
                if gc:
                    lat, lng = gc
                    geo_source = "places"
                    n_geo += 1

        if geo_source is None:
            n_none += 1
            continue

        for i in idxs:
            m = dict(metas[i])
            m["lat"], m["lng"], m["geo_source"] = lat, lng, geo_source
            upd_ids.append(ids[i])
            upd_meta.append(m)

    if upd_ids:
        coll.update(ids=upd_ids, metadatas=upd_meta)

    print(f"Geo-enriched: {n_gps} article(s) via GPS, {n_geo} via Places; "
          f"{n_none} had no usable location, {n_skip} already tagged (skipped).")
    print(f"Updated {len(upd_ids)} chunks. Query with, e.g.:\n"
          f'    python query.py "supper" --near "3.1390,101.6869" --radius-km 5')


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Geo-enrich the FoodRAG store.")
    ap.add_argument("--places", action="store_true",
                    help="also geocode Address: lines via Google (needs API key)")
    ap.add_argument("--overwrite", action="store_true",
                    help="re-derive coords even for already-tagged chunks")
    args = ap.parse_args()
    run(args.places, args.overwrite)
