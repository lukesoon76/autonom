#!/usr/bin/env python3
"""
Cloud Instagram refresh — self-contained (no eatlist/workbook dependency).

Fetches recent posts from Business/Creator handles + hashtags via the OFFICIAL
Instagram Graph API and upserts them STRAIGHT into the vector store the app
serves, deduped by permalink. Designed to run inside the web service (see
cloud_refresh.py) so it writes to the same Render persistent disk the app reads.

Dormant (returns a message, no error) unless IG_ACCESS_TOKEN + IG_USER_ID env
vars are set. Handles/hashtags come from config/instagram.json. No scraping.
"""
import hashlib
import os
import re
import time
from datetime import datetime, timezone

import requests
import yaml  # noqa: F401  (ensures pyyaml present; config is json but keep parity)

import ingest
from import_eatlist import maps_link

GRAPH = "https://graph.facebook.com/v21.0"
PIN, CLOCK = "📍", "⏰"
HOURS_RE = re.compile(r"\d{1,2}([:.]\d{2})?\s*(am|pm)?\s*[-–]\s*"
                      r"\d{1,2}([:.]\d{2})?\s*(am|pm)", re.I)
COUNTRY_REGION = [("singapore", "SG"), ("s'pore", "SG"), ("bangkok", "TH"),
                  ("thailand", "TH"), ("penang", "MY"), ("kuala lumpur", "MY"),
                  ("selangor", "MY"), ("malaysia", "MY"), ("ipoh", "MY"),
                  ("johor", "MY")]
CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "config", "instagram.json")


def creds():
    return (os.environ.get("IG_ACCESS_TOKEN", "").strip(),
            os.environ.get("IG_USER_ID", "").strip())


def _conf():
    import json
    try:
        c = json.load(open(CONF_PATH))
    except (ValueError, OSError):
        c = {}
    return (c.get("handles", []), c.get("hashtags", []),
            int(c.get("max_per_source", 10)))


def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def parse_caption(caption):
    lines = [ln.strip() for ln in (caption or "").splitlines() if ln.strip()]
    address = hours = ""
    for ln in lines:
        if PIN in ln and not address:
            address = _clean(ln.split(PIN, 1)[1])
        if not hours and (CLOCK in ln or HOURS_RE.search(ln)):
            hours = _clean(ln.split(CLOCK, 1)[1] if CLOCK in ln else ln)
    name = ""
    for ln in lines:
        t = _clean(re.sub(r"[^\w\s&'\-/().]", "", re.sub(r"[#@]\S+", "", ln)))
        if len(t) >= 3:
            name = t[:70]
            break
    low = (caption or "").lower()
    region = next((code for k, code in COUNTRY_REGION if k in low), "MY")
    return name, region, address, hours


def _media_to_meta(m, default_name=""):
    cap = m.get("caption", "") or ""
    name, region, address, hours = parse_caption(cap)
    name = name or default_name
    if not name:
        return None
    permalink = m.get("permalink", "")
    return {
        "doc": _clean(cap)[:400] or name,
        "meta": {"source": "Instagram (Graph API)", "region": region,
                 "city": address.split(",")[-1].strip() if address else "",
                 "url": permalink, "title": name, "image": "", "priority": 2,
                 "cuisine": "", "food_type": "", "accolades": "", "price": "",
                 "hours": hours, "address": address, "phone": "",
                 "maps": maps_link(name, address, ""), "order": "",
                 "tier": "Instagram", "rating": 0.0,
                 "date": (m.get("timestamp", "") or "")[:10],
                 "ingested": datetime.now(timezone.utc).isoformat()},
        "id": "igcloud#" + hashlib.sha1(
            (permalink or f"{name}|{address}").encode()).hexdigest(),
    }


def _fetch_handle(handle, uid, tok, n):
    fields = (f"business_discovery.username({handle})"
              f"{{media.limit({n}){{caption,permalink,timestamp}}}}")
    r = requests.get(f"{GRAPH}/{uid}",
                     params={"fields": fields, "access_token": tok}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(r.json().get("error", {}).get("message", r.text)[:160])
    data = r.json().get("business_discovery", {}).get("media", {}).get("data", [])
    return [x for x in (_media_to_meta(m, handle) for m in data) if x]


def _fetch_hashtag(tag, uid, tok, n):
    r = requests.get(f"{GRAPH}/ig_hashtag_search",
                     params={"user_id": uid, "q": tag, "access_token": tok}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(r.json().get("error", {}).get("message", r.text)[:160])
    ids = r.json().get("data", [])
    if not ids:
        return []
    r2 = requests.get(f"{GRAPH}/{ids[0]['id']}/recent_media",
                      params={"user_id": uid, "fields": "caption,permalink,timestamp",
                              "access_token": tok, "limit": n}, timeout=30)
    if r2.status_code != 200:
        raise RuntimeError(r2.json().get("error", {}).get("message", r2.text)[:160])
    return [x for x in (_media_to_meta(m) for m in r2.json().get("data", [])) if x]


def run(embedder=None, coll=None):
    """Fetch + upsert into the vector store. Returns (added, message)."""
    tok, uid = creds()
    if not (tok and uid):
        return 0, "Instagram not configured (set IG_ACCESS_TOKEN + IG_USER_ID)."
    handles, hashtags, n = _conf()
    embedder = embedder or ingest.get_embedder()
    coll = coll or ingest.get_collection()
    items, seen = [], set()
    for h in handles:
        try:
            got = _fetch_handle(h.lstrip("@"), uid, tok, n)
        except Exception as e:
            print(f"[ig_cloud] @{h}: {e}")
            got = []
        items += got
        time.sleep(1)
    for tag in hashtags:
        try:
            got = _fetch_hashtag(tag.lstrip("#"), uid, tok, n)
        except Exception as e:
            print(f"[ig_cloud] #{tag}: {e}")
            got = []
        items += got
        time.sleep(1)
    uniq = []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        uniq.append(it)
    if not uniq:
        return 0, "No new Instagram posts fetched."
    coll.upsert(ids=[it["id"] for it in uniq],
                documents=[it["doc"] for it in uniq],
                embeddings=embedder.encode([it["doc"] for it in uniq]).tolist(),
                metadatas=[it["meta"] for it in uniq])
    return len(uniq), f"Upserted {len(uniq)} Instagram posts into the core."


if __name__ == "__main__":
    added, msg = run()
    print(msg)
