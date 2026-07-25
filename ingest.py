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
from xml.etree import ElementTree

import feedparser
import requests
import trafilatura
import yaml
import chromadb
from protego import Protego
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

_robots_cache: dict[str, object] = {}   # host -> Protego | _ROBOTS_DENY_ALL | None
_last_hit: dict[str, float] = {}


# ── politeness helpers ──────────────────────────────────────────────────────
_ROBOTS_DENY_ALL = "DENY_ALL"       # sentinel: robots.txt itself was 401/403


def _load_robots(scheme: str, host: str):
    """Fetch and parse a host's robots.txt with OUR descriptive User-Agent.

    We deliberately do NOT let the parser fetch robots.txt itself: the stdlib's
    RobotFileParser.read() uses urllib's default User-Agent, which many
    Cloudflare-fronted sites 403. Since a 403 on robots.txt means "disallow all"
    per the standard, that quirk would block an entire site based on a
    bot-challenge error page rather than its real policy. Fetching with our own
    honest UA reads the true rules.

    Parsing uses Protego (the parser Scrapy uses): full wildcard (`*`, `$`) and
    Allow-vs-Disallow specificity support, unlike the stdlib parser.

    Returns a Protego instance, the _ROBOTS_DENY_ALL sentinel (robots forbidden
    → deny), or None when robots is unreachable (network error / 5xx) — the
    caller treats None as cautious-allow, per spec.
    """
    robots_url = f"{scheme}://{host}/robots.txt"
    try:
        r = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=15)
    except Exception:
        return None                      # unreachable → cautious-allow
    if r.status_code in (401, 403):
        return _ROBOTS_DENY_ALL          # access to robots forbidden → deny all
    if 400 <= r.status_code < 500:
        return Protego.parse("")         # e.g. 404: no robots published → allow all
    if r.status_code >= 500:
        return None                      # server error → treat as unreachable
    return Protego.parse(r.text)


def allowed_by_robots(url: str) -> bool:
    """Check robots.txt for `url`, caching one parser per host.

    If robots.txt is unreachable we default to cautious-allow, but any explicit
    Disallow (and a 401/403-protected robots.txt) is always respected.
    """
    parts = urlparse(url)
    host = parts.netloc
    if host not in _robots_cache:
        _robots_cache[host] = _load_robots(parts.scheme, host)
    rp = _robots_cache[host]
    if rp is None:
        return True                      # unreachable → cautious-allow
    if rp is _ROBOTS_DENY_ALL:
        return False
    return rp.can_fetch(url, USER_AGENT)


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
def parse_feed(url: str):
    """Parse an RSS/Atom feed, always sending our descriptive User-Agent.

    feedparser's built-in fetch uses a generic UA that some hosts (Cloudflare,
    a few WordPress installs) reject with an empty or error body. We first ask
    feedparser to fetch *with our UA*; if that still yields no entries, we fetch
    the bytes ourselves (same honest UA — no browser spoofing, no evasion) and
    parse those. Returns a feedparser result whose `.entries` may be empty.
    """
    parsed = feedparser.parse(url, agent=USER_AGENT)
    if parsed.entries:
        return parsed
    # Fallback: fetch the bytes ourselves with the same UA, then parse them.
    throttle(url)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        if r.ok and r.content:
            refetched = feedparser.parse(r.content)
            if refetched.entries:
                return refetched
    except Exception:
        pass
    return parsed  # best effort (entries likely empty)


def discover_feed(site_url: str) -> str | None:
    """Try common feed paths for a site and return the first that parses."""
    base = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    for path in FEED_PROBE_PATHS:
        candidate = base + path
        if parse_feed(candidate).entries:
            return candidate
    return None


def urls_from_rss(url: str, limit: int) -> list[dict]:
    parsed = parse_feed(url)
    out = []
    for e in parsed.entries[:limit]:
        out.append({
            "url": e.get("link"),
            "title": e.get("title", ""),
            "date": e.get("published", e.get("updated", "")),
        })
    return out


def _read_sitemap(url: str) -> tuple[str | None, list[str]]:
    """Fetch a sitemap and return (root_tag_localname, [<loc> texts]).

    Works for both a leaf `<urlset>` (loc = article URLs) and a
    `<sitemapindex>` (loc = child sitemap URLs).
    """
    xml = fetch(url)
    if not xml:
        return None, []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None, []
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [loc.text for loc in root.iterfind(".//sm:loc", ns) if loc.text]
    local = root.tag.split("}")[-1]  # strip {namespace}
    return local, locs


def urls_from_sitemap(url: str, url_filter: str, limit: int) -> list[dict]:
    kind, locs = _read_sitemap(url)
    if kind == "sitemapindex":
        # Descend into child sitemaps, in order, until we have `limit` matches.
        collected: list[str] = []
        for child in locs:
            _, child_locs = _read_sitemap(child)
            collected.extend(u for u in child_locs if url_filter in u)
            if len(collected) >= limit:
                break
        locs = collected
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
            if s["type"] == "rss":
                if parse_feed(s["url"]).entries:
                    print(f"[ok]      {s['name']}: {s['url']}")
                else:
                    found = discover_feed(s["url"])
                    print(f"[repair?] {s['name']}: {s['url']} -> {found or 'NOT FOUND'}")
            elif s["type"] == "sitemap":
                n = len(urls_from_sitemap(s["url"], s.get("url_filter", ""), limit))
                tag = "[ok]     " if n else "[empty?] "
                print(f"{tag} {s['name']} (sitemap): {n} urls match "
                      f"'{s.get('url_filter', '')}' in {s['url']}")
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
