#!/usr/bin/env python3
"""
Bulk-add sources — the fast way to grow the corpus across ASEAN.

Give it any mix of: an OPML file (export from any feed reader), a text file of
site homepages / feed URLs (one per line, `#` comments ok), or URLs on the
command line. For each site it finds a working RSS/Atom feed (probing common
paths with our real User-Agent), and appends the good ones to
config/user_sources.yaml — so they persist AND get picked up by the daily
refresh. Nothing is scraped here; it only *discovers and registers* feeds.

    python import_sources.py --urls sites.txt --region TH --priority 2
    python import_sources.py --opml feeds.opml --region ID
    python import_sources.py https://migrationology.com https://saigoneer.com --region TH
    python import_sources.py --urls sites.txt --dry-run     # just report, don't save

A line in --urls may override region/priority inline, e.g.:
    https://pepper.ph | PH | 2
"""
import argparse
import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from xml.etree import ElementTree

import ingest

socket.setdefaulttimeout(12)   # keep feedparser/urllib from hanging on dead hosts


def _domain_name(url: str) -> str:
    net = urlparse(url).netloc.replace("www.", "")
    return net.split(".")[0].replace("-", " ").title() if net else url


def from_opml(path: str) -> list[dict]:
    root = ElementTree.parse(path).getroot()
    out = []
    for o in root.iter("outline"):
        feed = o.get("xmlUrl")
        if feed:
            out.append({"url": feed, "name": o.get("title") or o.get("text") or "",
                        "is_feed": True})
    return out


def from_urls_file(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            out.append({"url": parts[0],
                        "region": parts[1] if len(parts) > 1 else None,
                        "priority": int(parts[2]) if len(parts) > 2 and parts[2] else None,
                        "name": parts[3] if len(parts) > 3 else ""})
    return out


def _youtube_feed(url: str) -> str | None:
    """Resolve a YouTube channel/handle URL to its official videos.xml feed."""
    if "feeds/videos.xml" in url:
        return url if ingest.parse_feed(url).entries else None
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{20,})", url)
    if not m:
        html = ingest.fetch(url)          # robots-gated GET
        if not html:
            return None
        m = re.search(r'"(?:channelId|externalId)":"(UC[0-9A-Za-z_-]{20,})"', html) \
            or re.search(r"/channel/(UC[0-9A-Za-z_-]{20,})", html)
    if not m:
        return None
    feed = f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"
    return feed if ingest.parse_feed(feed).entries else None


def _safe_resolve(item: dict):
    try:
        return resolve_feed(item)
    except Exception:
        return None


def resolve_feed(item: dict) -> str | None:
    """Return a working feed URL for the item, or None."""
    url = item["url"]
    if "youtube.com" in url or "youtu.be" in url:
        return _youtube_feed(url)
    if item.get("is_feed") or url.rstrip("/").endswith(("feed", "rss", "atom.xml")) \
            or "?feed=" in url or url.endswith(".xml"):
        return url if ingest.parse_feed(url).entries else ingest.discover_feed(url)
    # a homepage → probe for its feed
    if ingest.parse_feed(url).entries:      # some homepages ARE feeds
        return url
    return ingest.discover_feed(url)


def run(items, region, priority, dry_run):
    existing = {s.get("url") for s in ingest.load_user_sources()}
    added = skipped = notfound = 0
    # resolve feeds concurrently (bounded by socket timeout) — much faster
    with ThreadPoolExecutor(max_workers=12) as ex:
        resolved = list(ex.map(lambda it: (it, _safe_resolve(it)), items))
    for it, feed in resolved:
        reg = it.get("region") or region or ""
        pri = it.get("priority") or priority
        name = it.get("name") or _domain_name(it["url"])
        if not feed:
            print(f"[NOT FOUND] {it['url']}")
            notfound += 1
            continue
        if feed in existing:
            print(f"[exists]    {name}: {feed}")
            skipped += 1
            continue
        if dry_run:
            print(f"[would add] {name} ({reg}, p{pri}): {feed}")
        else:
            ingest.add_user_source(name, feed, type="rss", region=reg,
                                   city="", priority=pri)
            print(f"[added]     {name} ({reg}, p{pri}): {feed}")
        added += 1
    verb = "would add" if dry_run else "added"
    print(f"\nDone. {verb} {added}, {skipped} already present, {notfound} not found.")
    if not dry_run and added:
        print("Ingest them now with:  python ingest.py --min-priority 2 "
              "   (or wait for the daily refresh)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Bulk-discover & register feed sources.")
    ap.add_argument("urls", nargs="*", help="site homepages or feed URLs")
    ap.add_argument("--opml", help="OPML file to import")
    ap.add_argument("--urls", dest="urls_file", help="text file of URLs (one per line)")
    ap.add_argument("--region", default="", help="default region tag (e.g. TH, ID, PH)")
    ap.add_argument("--priority", type=int, default=2, help="default priority (1-3)")
    ap.add_argument("--dry-run", action="store_true", help="report only; don't save")
    args = ap.parse_args()

    items = [{"url": u, "name": ""} for u in args.urls]
    if args.opml:
        items += from_opml(args.opml)
    if args.urls_file:
        items += from_urls_file(args.urls_file)
    if not items:
        ap.error("give some URLs, --opml, or --urls FILE")
    try:
        run(items, args.region, args.priority, args.dry_run)
    except KeyboardInterrupt:
        sys.exit(130)
