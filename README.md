# ChiefEpicure (FoodRAG) — where to eat in Malaysia & Singapore

A small, **local** retrieval-augmented "where to eat" system. It ingests
food-blog RSS feeds and sitemaps for Kuala Lumpur and Singapore into a vector
database, then answers free-form questions — *"where's good laksa in KL?"*,
*"natural wine bars in Singapore?"* — grounded in that corpus, **with source
links**. Embeddings run locally (no API cost); Claude is used only to phrase the
final answer, and retrieval works fine without it.

## Architecture

```
                          config/sources.yaml
                       (RSS · sitemap · manual)
                                  │
          ┌───────────────────────┴───────────────────────┐
          │                                                 │
     ingest.py                                     curate_authority.py
  RSS / sitemap URLs                          config/curated_authority.csv
          │  robots.txt check + 1.5s/host throttle           │  (Michelin / 50 Best,
          ▼                                                   ▼   never scraped)
  trafilatura extract ──► chunk (900/150) ──► embed ──► embed one line per row
          │                    all-MiniLM-L6-v2 (384-dim, local)  │
          └───────────────────────┬───────────────────────────────┘
                                   ▼
                        Chroma (./chroma_db)
                    collection "food_reviews"
                    ids = sha1(url#chunk) → upsert (idempotent)
                                   │
                                   ▼
                               query.py
             embed question ─► vector search (+region/city filter)
                                   │
                     ┌─────────────┴─────────────┐
              ANTHROPIC_API_KEY?             (no key)
                     │                           │
        Claude answer, ONLY from          raw ranked snippets
        snippets, each pick cited                │
                     └─────────────┬─────────────┘
                                   ▼
                     answer + de-duplicated Sources list
```

## Setup

Requires Python 3.11+.

```bash
# from the project root (~/ChiefEpicure)
python -m venv .venv && source .venv/bin/activate   # or: uv venv --python 3.12 .venv
pip install -r requirements.txt                     # or: uv pip install -r requirements.txt

cp .env.example .env          # optional — only needed for generated answers
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

The first ingest or query downloads the `all-MiniLM-L6-v2` embedding model
(~90 MB) once, then runs fully offline for retrieval.

## The flow: discover → ingest → query

**1. Discover / repair feeds.** Several `/feed/` URLs in `sources.yaml` are
best-guess WordPress paths. Probe them before the first ingest:

```bash
python ingest.py --discover
```

Prints one line per RSS source — `[ok]` with the working URL, or `[repair?]`
with a suggested replacement (or `NOT FOUND`). Update `config/sources.yaml`
with any repairs, then ingest.

**2. Ingest.** Fetch, extract, chunk, embed, and upsert into `./chroma_db`:

```bash
python ingest.py                     # everything in sources.yaml
python ingest.py --region SG         # Singapore only (choices: SG, MY)
python ingest.py --min-priority 2    # skip priority-3 sources
python ingest.py --limit 3           # cap articles per source (default 15)
```

Re-running is **idempotent**: chunk ids are `sha1(url#index)` and writes use
`upsert`, so nothing is duplicated.

**3. Add authoritative picks (optional).** Michelin / Asia's 50 Best are never
scraped — curate them by hand in `config/curated_authority.csv`, then load them
into the same collection:

```bash
python curate_authority.py
```

**4. Ask.** Retrieve, then answer with citations:

```bash
python query.py "good char kway teow in KL" --region MY
python query.py "best brunch cafes" --city "Kuala Lumpur"
python query.py "new omakase worth booking" --region SG --k 8
```

With `ANTHROPIC_API_KEY` set you get a written recommendation, each pick cited
`[Source]`. Without a key it degrades gracefully to the raw ranked snippets.
Either way it ends with a de-duplicated `Sources:` list.

## Web UI (Streamlit)

For a friendlier front-end over the same pipeline:

```bash
streamlit run app.py
```

- **🔎 Find food** — search box + region/city filters + a results slider; shows
  a cited Claude answer (when a key is set) or ranked snippet cards, each linking
  to its source.
- **➕ Add a source** — paste any **website / article / RSS feed / sitemap URL**
  and ingest it live into the same store. It runs through the *identical* polite
  pipeline (robots.txt + rate limits respected, bot detection never bypassed) and
  is idempotent, so re-adding never duplicates.

The same "add your own URL" capability is available headlessly:

```python
from ingest import ingest_user_source
ingest_user_source("https://example.com/best-nasi-lemak/", region="MY", city="Kuala Lumpur")
```

## Skipping sponsored / PR content

These feeds mix real reviews with brand PR ("Samsung Unveils…", mooncake gift
sets, airline advertorials). `content_filter.py` flags those **precision-first**
(explicit sponsorship disclosures + unambiguous corporate-announcement titles),
and `ingest.py` skips them by default:

```bash
python ingest.py                    # sponsored/PR skipped automatically
python ingest.py --keep-sponsored   # store them too (tagged sponsored=True)
python ingest.py --prune-sponsored  # delete already-stored PR from the DB, then exit
```

Kept chunks carry a `sponsored` metadata flag. Tune the phrase/title lists in
`content_filter.py` — err toward keeping a borderline post over dropping a real
review.

## Geo "nearby"

Give places coordinates, then rank by distance:

```bash
python enrich_geo.py                 # extract GPS coords printed in posts (free, no key)
python enrich_geo.py --places        # also geocode Address: lines (needs GOOGLE_PLACES_API_KEY)
python query.py "supper" --near "3.1390,101.6869" --radius-km 5
```

`enrich_geo.py` writes `lat`/`lng`/`geo_source` onto each article's chunks (idempotent).
`query.py --near "lat,lng" [--radius-km N]` over-fetches candidates, drops those
without coords or outside the radius, and sorts by proximity (distance shown).
The Streamlit sidebar has the same under **📍 Near a point**. ChiefEater and
KY Speaks place pages (which print `GPS:` lines) are the best seed.

## Keep it fresh (daily refresh)

Websites are safe to refresh **daily** — ingestion is idempotent, and RSS/
sitemaps only surface *recent* posts, so a nightly run catches new articles with
no duplication. Minute-level "real-time" buys nothing for blogs that publish a
few times a day.

**macOS (launchd — native, recommended).** A LaunchAgent runs the priority-1&2
refresh daily at 04:30:

```bash
# installed at ~/Library/LaunchAgents/com.chiefepicure.refresh.plist
launchctl load -w ~/Library/LaunchAgents/com.chiefepicure.refresh.plist   # enable
launchctl start com.chiefepicure.refresh                                   # run now (test)
launchctl unload ~/Library/LaunchAgents/com.chiefepicure.refresh.plist     # disable
```

**Linux (cron).** Equivalent nightly line:

```cron
30 4 * * * cd ~/ChiefEpicure && .venv/bin/python ingest.py --min-priority 2 >> ~/ChiefEpicure/ingest.log 2>&1
```

### Social media (Instagram / TikTok / Facebook / X)

**Not supported by scraping — by design.** These platforms' robots.txt and Terms
prohibit automated scraping, require login, and run bot-detection that this
project will not bypass. The compliant path, if you need social later, is the
platforms' **official APIs** (Instagram Graph, YouTube Data, TikTok Display) for
accounts you own or are authorised to pull, or licensed data vendors — a future
`enrich_social.py` module, not a scraper.

## Legal & etiquette

This tool is built for **personal** retrieval, and it stays polite by design:

- **robots.txt is respected** for every fetched article URL, one cached parser
  per host. We fetch robots.txt with our *own* descriptive User-Agent (not
  urllib's default, which Cloudflare-fronted sites 403 — that quirk makes the
  stdlib parser disallow the whole site off an error page) and parse it with
  **Protego** for correct wildcard (`*`, `$`) and Allow-precedence handling. If
  robots is unreachable it defaults to cautious-allow; any explicit `Disallow`,
  and a genuine 401/403-protected robots.txt, is always honoured.
- **Rate-limited** to ≥1.5s between requests to the same host, with a descriptive
  `User-Agent: FoodRAG/1.0 (personal research bot; respects robots.txt)`.
- **Copyright:** only short snippets + link + metadata are stored, for personal
  retrieval. The code never republishes full article text.
- **Manual sources** (Michelin, Asia's 50 Best) are **never scraped** — they load
  only from the curated CSV (name / city / stars / cuisine / url).

Please keep these guarantees intact if you extend the crawler.

## Stretch goals

1. ~~**Geo "nearby"**~~ — ✅ shipped (`enrich_geo.py` + `query.py --near`).
   Next: geocode more via Places, and per-*place* coords for listicles (one
   article can mention many restaurants → currently one coord per article).
2. **Recency weighting** — boost chunks whose `date` is within the last N months.
3. ~~**Streamlit UI**~~ — ✅ shipped (`app.py`).
4. **Cross-blog dedup** — when several sources cover the same restaurant, merge
   in the answer and list all sources.
5. **Content quality** — the PR filter (`content_filter.py`) is precision-first;
   a broader food-vs-nonfood classifier would catch more soft-PR listicles.
6. **Per-place region tags** — `region` is per-source today, so an SG blog's KL
   writeup is tagged SG; infer region from the place/address instead.

## Project layout

```
ChiefEpicure/
  config/
    sources.yaml            # source registry (RSS / sitemap / manual)
    curated_authority.csv   # hand-filled Michelin / 50 Best rows
  ingest.py                 # RSS + sitemap + page -> extract -> chunk -> embed -> Chroma
  content_filter.py         # sponsored/PR detection (used by ingest + prune)
  curate_authority.py       # load curated_authority.csv into the same collection
  enrich_geo.py             # add lat/lng to chunks (GPS extraction + optional geocode)
  query.py                  # retrieve (+filters, +--near) -> answer via Claude -> cite
  app.py                    # Streamlit web UI (search + geo + add-your-own-URL)
  requirements.txt
  .env.example
  chroma_db/                # created on first ingest (git-ignored)
```
