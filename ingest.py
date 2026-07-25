#!/usr/bin/env python3
"""
FoodRAG ingestion.

Reads config/sources.yaml, pulls new articles from RSS feeds and sitemaps,
politely fetches full text (respecting robots.txt + rate limits), extracts the
main content, chunks it, embeds it locally, and upserts into a persistent
Chroma vector store.

Usage:
    python ingest.py                    # ingest everything in sources.yaml
    python ingest.py --region SG        # only Singapore sources (choices: SG, MY)
    python ingest.py --min-priority 2   # skip priority-3 sources
    python ingest.py --limit 20         # cap articles per source (default 15)
    python ingest.py --discover         # probe/repair feed URLs, then exit

Re-running is safe: chunk IDs are content-stable, so nothing is duplicated
(collection.upsert, never add). Schedule it with cron (see README) to keep the
DB fresh.
"""
import argparse
import hashlib
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import feedparser
import requests
import trafilatura
import yaml
import chromadb
from sentence_transformers import SentenceTransformer

USER_AGENT = "FoodRAG/1.0 (personal research bot; respects robots.txt)"
REQUEST_DELAY = 1.5            # seconds between fetches to the same host
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
EMBED_MODEL = "all-MiniLM-L6-v2"   # local, free, 384-dim
DB_PATH = "./chroma_db"
COLLECTION = "food_reviews"
SOURCES_PATH = "config/sources.yaml"

# Common feed paths tried by --discover, in order of likelihood.
FEED_PROBE_PATHS = (
    "/feed/",
    "/rss/",
    "/feed",
    "/?feed=rss2",
    "/atom.xml",
    "/feeds/posts/default?alt=rss",   # Blogger default
)

_robots_cache: dict[str, RobotFileParser | None] = {}
_last_hit: dict[str, float] = {}


# ── politeness helpers ──────────────────────────────────────────────────────
def allowed_by_robots(url: str) -> bool:
    """Check robots.txt for `url`, caching one parser per host.

    If robots.txt is unreachable we default to cautious-allow (the site simply
    hasn't published rules), but any explicit Disallow is always respected.
    """
    host = urlparse(url).netloc
    if host not in _robots_cache:
        rp = RobotFileParser()
        rp.set_url(f"{urlparse(url).scheme}://{host}/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None  # if robots is unreachable, default to cautious-allow
        _robots_cache[host] = rp
    rp = _robots_cache[host]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def throttle(url: str) -> None:
    """Ensure >= REQUEST_DELAY seconds between hits to the same host."""
    host = urlparse(url).netloc
    wait = REQUEST_DELAY - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()


def fetch(url: str, timeout: int = 20) -> str | None:
    if not allowed_by_robots(url):
        print(f"    robots.txt disallows {url} — skipping")
        return None
    throttle(url)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"    fetch failed: {e}")
        return None


# ── source readers ──────────────────────────────────────────────────────────
def discover_feed(site_url: str) -> str | None:
    """Try common feed paths for a site and return the first that parses."""
    base = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    for path in FEED_PROBE_PATHS:
        candidate = base + path
        parsed = feedparser.parse(candidate)
        if parsed.entries:
            return candidate
    return None


def urls_from_rss(url: str, limit: int) -> list[dict]:
    parsed = feedparser.parse(url)
    out = []
    for e in parsed.entries[:limit]:
        out.append({
            "url": e.get("link"),
            "title": e.get("title", ""),
            "date": e.get("published", e.get("updated", "")),
        })
    return out


def urls_from_sitemap(url: str, url_filter: str, limit: int) -> list[dict]:
    xml = fetch(url)
    if not xml:
        return []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [loc.text for loc in root.iterfind(".//sm:loc", ns) if loc.text]
    filtered = [u for u in locs if url_filter in u][:limit]
    return [{"url": u, "title": "", "date": ""} for u in filtered]


# ── text processing ─────────────────────────────────────────────────────────
def extract_text(html: str, url: str) -> str | None:
    return trafilatura.extract(html, url=url, favor_precision=True)


def chunk(text: str) -> list[str]:
    """Normalize whitespace, then slice into overlapping character windows."""
    text = " ".join(text.split())
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i:i + CHUNK_CHARS])
        i += CHUNK_CHARS - CHUNK_OVERLAP
    return chunks


def stable_id(url: str, idx: int) -> str:
    return hashlib.sha1(f"{url}#{idx}".encode()).hexdigest()


# ── main ────────────────────────────────────────────────────────────────────
def load_sources(path: str, region: str | None, min_priority: int | None) -> list[dict]:
    with open(path) as f:
        srcs = yaml.safe_load(f)["sources"]
    if region:
        srcs = [s for s in srcs if s.get("region") == region]
    if min_priority is not None:
        # priority 1 = most important; --min-priority 2 keeps 1 and 2, drops 3.
        srcs = [s for s in srcs if s.get("priority", 99) <= min_priority]
    return srcs


def run(region, limit, min_priority, discover):
    sources = load_sources(SOURCES_PATH, region, min_priority)

    if discover:
        for s in sources:
            if s["type"] != "rss":
                continue
            ok = bool(feedparser.parse(s["url"]).entries)
            if ok:
                print(f"[ok]      {s['name']}: {s['url']}")
            else:
                found = discover_feed(s["url"])
                print(f"[repair?] {s['name']}: {s['url']} -> {found or 'NOT FOUND'}")
        return

    embedder = SentenceTransformer(EMBED_MODEL)
    client = chromadb.PersistentClient(path=DB_PATH)
    coll = client.get_or_create_collection(COLLECTION)

    for s in sources:
        print(f"\n=== {s['name']} ({s['region']}/{s.get('city','')}) ===")
        if s["type"] == "rss":
            items = urls_from_rss(s["url"], limit)
        elif s["type"] == "sitemap":
            items = urls_from_sitemap(s["url"], s.get("url_filter", ""), limit)
        else:
            print("    manual source — import via curate_authority.py, skipping here")
            continue

        for it in items:
            url = it["url"]
            if not url:
                continue
            html = fetch(url)
            if not html:
                continue
            text = extract_text(html, url)
            if not text or len(text) < 300:
                continue

            pieces = chunk(text)
            ids = [stable_id(url, i) for i in range(len(pieces))]
            metas = [{
                "source": s["name"],
                "region": s["region"],
                "city": s.get("city", ""),
                "url": url,
                "title": it["title"],
                "date": it["date"],
                "priority": s.get("priority", 99),
                "ingested": datetime.now(timezone.utc).isoformat(),
            } for _ in pieces]
            embeddings = embedder.encode(pieces).tolist()
            coll.upsert(ids=ids, documents=pieces, embeddings=embeddings, metadatas=metas)
            print(f"    + {it['title'][:60] or url[:60]}  ({len(pieces)} chunks)")

    print(f"\nDone. Collection now holds {coll.count()} chunks at {DB_PATH}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="FoodRAG ingestion (MY & SG food blogs).")
    ap.add_argument("--region", choices=["SG", "MY"], default=None,
                    help="restrict to one region")
    ap.add_argument("--min-priority", type=int, default=None, dest="min_priority",
                    help="keep sources with priority <= N (e.g. 2 skips priority-3)")
    ap.add_argument("--limit", type=int, default=15, help="max articles per source")
    ap.add_argument("--discover", action="store_true",
                    help="probe/repair feeds then exit (no ingest)")
    args = ap.parse_args()
    try:
        run(args.region, args.limit, args.min_priority, args.discover)
    except KeyboardInterrupt:
        sys.exit(130)
