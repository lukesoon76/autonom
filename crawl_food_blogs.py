#!/usr/bin/env python3
"""
crawl_food_blogs.py - harvest venue entries from SG/MY food blogs into a CSV.

Most of these sites run WordPress or Blogger, so instead of scraping HTML we ask
their APIs for clean JSON. That is faster, far more robust, and much politer.

    WordPress  ->  /wp-json/wp/v2/posts?per_page=100&page=N
    Blogger    ->  /feeds/posts/default?alt=json&max-results=150&start-index=N

Usage
-----
    pip install requests beautifulsoup4 lxml
    python crawl_food_blogs.py --list
    python crawl_food_blogs.py --site eatdrinkkl --max-pages 40
    python crawl_food_blogs.py --site sethlui --site eatbook --max-pages 60
    python crawl_food_blogs.py --all --max-pages 25          # a first sweep of everything

Output
------
    crawl_output/<site>.csv     one row per post, appended incrementally
    crawl_output/<site>.state   resume marker, so re-running continues where it stopped

Then feed the CSVs to merge_into_master.py.

Be a good citizen: the defaults below are deliberately slow (1.5s between
requests). Do not lower DELAY below ~1s, and do not run several sites in
parallel against the same host.
"""

import argparse, csv, html, json, os, re, sys, time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; PersonalFoodListBot/1.0; +personal research, low volume)"
DELAY = 1.5           # seconds between requests
TIMEOUT = 25
OUTDIR = "crawl_output"

# --------------------------------------------------------------------------
# Site registry.  kind: "wp" (WordPress REST) | "blogger" (Blogger feed)
# country: default country when the text gives no other clue.
# --------------------------------------------------------------------------
SITES = {
    "eatdrinkkl":   dict(base="https://www.eatdrinkkl.com",      kind="blogger", country="Malaysia"),
    "johorkaki":    dict(base="https://www.johorkaki.blogspot.com", kind="blogger", country="Malaysia"),
    "sethlui":      dict(base="https://sethlui.com",             kind="wp", country=None),
    "eatbook":      dict(base="https://eatbook.sg",              kind="wp", country="Singapore"),
    "misstamchiak": dict(base="https://www.misstamchiak.com",    kind="wp", country="Singapore"),
    "ieatishootipost": dict(base="https://ieatishootipost.sg",   kind="wp", country="Singapore"),
    "danielfooddiary": dict(base="https://danielfooddiary.com",  kind="wp", country=None),
    "ladyironchef": dict(base="https://www.ladyironchef.com",    kind="wp", country="Singapore"),
    "vkeong":       dict(base="https://www.vkeong.com",          kind="wp", country="Malaysia"),
    "kyspeaks":     dict(base="https://kyspeaks.com",            kind="wp", country="Malaysia"),
    "bangsarbabe":  dict(base="https://www.bangsarbabe.com",     kind="wp", country="Malaysia"),
    "chiefeater":   dict(base="https://chiefeater.com",          kind="wp", country="Malaysia"),
}

# --------------------------------------------------------------------------
# Field extraction
# --------------------------------------------------------------------------
# Singapore addresses end in "Singapore 123456"; Malaysian ones carry a 5-digit
# postcode followed by a state/territory.  Anchor on those.
RE_POST_SG = re.compile(r"\bSingapore\s+\d{6}\b")
RE_POST_MY = re.compile(
    r"\b\d{5}\s+[A-Z][A-Za-z '\-]{2,30}"
    r"(?:,\s*(?:Wilayah Persekutuan[A-Za-z ]{0,25}|Selangor|Johor|Penang|Pulau Pinang|Perak|Melaka|"
    r"Negeri Sembilan|Kedah|Kelantan|Terengganu|Pahang|Perlis|Sabah|Sarawak|Putrajaya))?")
# an address almost always starts at a unit/house number, "No.", "Lot" or "Jalan"
RE_ADDR_START = re.compile(r"(?:\b(?:No\.?|Lot|Unit|Blk|Block)\s*)?#?\d+[A-Za-z]?(?:[-/]\d+[A-Za-z]?)*\s*,?\s+|"
                           r"\b(?:Jalan|Jln|Lorong|Lebuh|Persiaran|Medan|Taman|Bandar)\b")

def _address_from(text, postmatch, window=170):
    """Given a postcode match, walk back to the most plausible address start."""
    end = postmatch.end()
    lo = max(0, postmatch.start() - window)
    chunk = text[lo:end]
    # never cross a sentence break or a bullet
    for sep in (". ", "! ", "? ", " | ", " • "):
        if sep in chunk[:-40]:
            chunk = chunk[chunk.rindex(sep, 0, len(chunk) - 40) + len(sep):]
    starts = [m.start() for m in RE_ADDR_START.finditer(chunk)]
    if starts:
        chunk = chunk[starts[0]:]
    return re.sub(r"\s+", " ", chunk).strip(" ,;-")

RE_PHONE = re.compile(r"(\+?6?0?1[0-9][\s\-]?\d{3,4}[\s\-]?\d{4}|\+?65[\s\-]?\d{4}[\s\-]?\d{4}|\b[689]\d{7}\b)")
RE_HOURS = re.compile(
    r"(\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Daily|Everyday)\b[^\n]{0,110}?"
    r"\d{1,2}(?:[.:]\d{2})?\s*(?:am|pm|AM|PM)[^\n]{0,60})")
RE_PRICE = re.compile(r"((?:RM|S\$|SGD|MYR)\s?\d[\d,.]*(?:\s*[-–]\s*(?:RM|S\$)?\s?\d[\d,.]*)?)")
RE_CLOSED = re.compile(r"permanently closed|has closed|closed down|ceased operations", re.I)

MY_HINTS = ("malaysia", "kuala lumpur", "selangor", "penang", "johor", "ipoh",
            "melaka", "malacca", "petaling", "puchong", "cheras", "rm")
SG_HINTS = ("singapore", "hawker centre", "s$", "sgd", "orchard", "tiong bahru",
            "tanjong pagar", "geylang", "bedok")

# Titles that are round-ups / news / non-venue posts
SKIP_TITLE = re.compile(
    r"\b(recipe|giveaway|promotion|promo code|deals?|guide to|things to do|"
    r"what to do|hotel review|staycation|travel guide|quiz|opinion|interview|"
    r"how to|top \d+ (?:things|places to visit)|closed down|awards|list of|workshop|pottery|class(?:es)?|gift|shopping|spa|massage)\b", re.I)


def clean(s):
    if not s:
        return ""
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def venue_name(title):
    """Strip the editorial tail off a post title to get a venue name."""
    t = clean(title)
    t = re.sub(r"\s*[\[\(].*?[\]\)]\s*$", "", t)          # trailing [Jul 2026]
    t = re.split(r"\s*[:–—]\s|\s+\|\s+", t)[0]            # 'Name: blurb'
    t = re.sub(r"^\d+\s*[.)]\s*", "", t)                  # '3. Name'
    t = re.sub(r"\s+(review|is good|shines)$", "", t, flags=re.I)
    return t.strip(" -–—,")


def guess_country(text, default):
    if default:
        return default
    low = text.lower()
    my = sum(low.count(h) for h in MY_HINTS)
    sg = sum(low.count(h) for h in SG_HINTS)
    if my == sg == 0:
        return ""
    return "Malaysia" if my >= sg else "Singapore"


def extract(text, default_country, title=""):
    """Pull structured fields out of a post body."""
    blob = f"{title} {text}"
    country = guess_country(blob, default_country)
    addr = ""
    m = RE_POST_SG.search(text)
    if m:
        addr, country = _address_from(text, m), "Singapore"
    if not addr:
        m = RE_POST_MY.search(text)
        if m:
            addr = _address_from(text, m)
            country = country or "Malaysia"
            if country != "Singapore":
                country = "Malaysia"

    phone = RE_PHONE.search(text)
    hours = RE_HOURS.search(text)
    price = RE_PRICE.search(text)
    return dict(
        country=country,
        address=addr,
        phone=clean(phone.group(1)) if phone else "",
        hours=clean(hours.group(1)) if hours else "",
        price=clean(price.group(1)) if price else "",
        closed="yes" if RE_CLOSED.search(blob) else "",
    )


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------
class Fetcher:
    def __init__(self, base):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.base = base
        self.rp = RobotFileParser()
        try:
            self.rp.set_url(urljoin(base, "/robots.txt"))
            self.rp.read()
        except Exception:
            self.rp = None

    def allowed(self, url):
        if not self.rp:
            return True
        try:
            return self.rp.can_fetch(UA, url)
        except Exception:
            return True

    def get(self, url, **kw):
        if not self.allowed(url):
            print(f"    robots.txt disallows {url}", file=sys.stderr)
            return None
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=TIMEOUT, **kw)
                if r.status_code == 429:
                    time.sleep(10 * (attempt + 1))
                    continue
                if r.status_code >= 500:
                    time.sleep(4 * (attempt + 1))
                    continue
                return r
            except requests.RequestException as e:
                print(f"    retry {attempt+1}: {e}", file=sys.stderr)
                time.sleep(4 * (attempt + 1))
        return None


def wp_posts(f, base, page):
    """One page of the WordPress REST API. Returns list of dicts or None if exhausted."""
    url = f"{base.rstrip('/')}/wp-json/wp/v2/posts"
    r = f.get(url, params={"per_page": 100, "page": page,
                           "_fields": "link,title,content,excerpt,date"})
    if r is None or r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, list) or not data:
        return None
    return [dict(url=p.get("link", ""),
                 title=clean((p.get("title") or {}).get("rendered", "")),
                 body=clean((p.get("content") or {}).get("rendered", "")),
                 date=(p.get("date") or "")[:10]) for p in data]


def blogger_posts(f, base, index, step=150):
    """One window of the Blogger JSON feed."""
    url = f"{base.rstrip('/')}/feeds/posts/default"
    r = f.get(url, params={"alt": "json", "max-results": step, "start-index": index})
    if r is None or r.status_code != 200:
        return None
    try:
        entries = r.json().get("feed", {}).get("entry", []) or []
    except ValueError:
        return None
    if not entries:
        return None
    out = []
    for e in entries:
        link = next((l["href"] for l in e.get("link", []) if l.get("rel") == "alternate"), "")
        out.append(dict(url=link,
                        title=clean(e.get("title", {}).get("$t", "")),
                        body=clean(e.get("content", {}).get("$t", "")),
                        date=(e.get("published", {}).get("$t", "") or "")[:10]))
    return out


COLS = ["source", "name", "post_title", "country", "area", "address", "phone",
        "hours", "price", "closed", "url", "date"]


def crawl(site, cfg, max_pages, outdir):
    os.makedirs(outdir, exist_ok=True)
    csv_path = os.path.join(outdir, f"{site}.csv")
    state_path = os.path.join(outdir, f"{site}.state")

    start = 1
    if os.path.exists(state_path):
        start = int(open(state_path).read().strip() or 1)
    seen = set()
    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                seen.add(row["url"])

    f = Fetcher(cfg["base"])
    new_fh = not os.path.exists(csv_path)
    fh = open(csv_path, "a", newline="", encoding="utf-8")
    w = csv.DictWriter(fh, fieldnames=COLS)
    if new_fh:
        w.writeheader()

    n = 0
    page = start
    for i in range(max_pages):
        if cfg["kind"] == "wp":
            batch = wp_posts(f, cfg["base"], page)
            nxt = page + 1
        else:
            batch = blogger_posts(f, cfg["base"], page)
            nxt = page + 150
        if batch is None:
            print(f"  [{site}] no more posts at {page}")
            break
        for p in batch:
            if not p["url"] or p["url"] in seen:
                continue
            seen.add(p["url"])
            if SKIP_TITLE.search(p["title"]):
                continue
            fields = extract(p["body"], cfg["country"], p["title"])
            if not fields["address"] and not fields["phone"]:
                continue          # almost certainly not a venue write-up
            w.writerow(dict(source=site,
                            name=venue_name(p["title"]),
                            post_title=p["title"],
                            area="",
                            url=p["url"], date=p["date"], **fields))
            n += 1
        fh.flush()
        open(state_path, "w").write(str(nxt))
        print(f"  [{site}] window {page}: +{n} venues so far")
        page = nxt
        time.sleep(DELAY)
    fh.close()
    print(f"  [{site}] wrote {n} new rows -> {csv_path}")
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", action="append", default=[], help="site key (repeatable)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--max-pages", type=int, default=20,
                    help="windows per site per run; re-run to continue")
    ap.add_argument("--outdir", default=OUTDIR)
    a = ap.parse_args()

    if a.list:
        for k, v in SITES.items():
            print(f"  {k:16} {v['kind']:8} {v['base']}")
        return

    targets = list(SITES) if a.all else a.site
    if not targets:
        ap.error("pass --site KEY, --all, or --list")

    total = 0
    for s in targets:
        if s not in SITES:
            print(f"unknown site: {s}", file=sys.stderr)
            continue
        print(f"[{s}] {SITES[s]['base']}")
        try:
            total += crawl(s, SITES[s], a.max_pages, a.outdir)
        except KeyboardInterrupt:
            print("\ninterrupted - state saved, re-run to resume")
            break
        except Exception as e:
            print(f"  [{s}] failed: {e}", file=sys.stderr)
    print(f"\nTotal new rows this run: {total}")
    print(f"Next: python merge_into_master.py --workbook Asia_Eateries_Master_List.xlsx "
          f"--csv {a.outdir}/*.csv")


if __name__ == "__main__":
    main()
