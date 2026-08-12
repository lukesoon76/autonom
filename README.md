# Makanapa (FoodRAG) — where to eat in Malaysia & Singapore

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
scraped — they're curated as **facts** (name / city / stars / cuisine / url),
*not* the guide's review prose (copyright). Two ways to add them:

- **In the app:** *Add a source → ⭐ Add a Michelin / authority pick* — type each
  entry straight from a printed guide (e.g. the MICHELIN Guide KL/Penang). It
  appends to `config/curated_authority.csv` and indexes it immediately.
- **By file:** edit `config/curated_authority.csv` (columns
  `name,city,region,stars,cuisine,url,note`; `url` optional), then:

```bash
python curate_authority.py
```

Either way they surface under **Today → ⭐ Michelin & authority picks** and in
search, tagged `Authority`.

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

It's built to feel like an **app**, not a search box — the default screen is a
daily feed, with search and a personal list alongside.

- **🏠 Today** — *"what's new & good in your city"*: authority (Michelin) picks
  plus the freshest blog finds, grouped by recency (This week / month / earlier),
  each an image card with a 🕘 relative date and a **🔖 Save** button. Aggregated
  from whatever the daily refresh has pulled. Includes **🎲 Surprise me** (one
  good pick for tonight) and a **📬 Today's digest** preview/download.
- **🏙️ Your city** (sidebar) — set a home region/city (and optional lat,lng);
  it's saved and scopes the Today feed and searches.
- **❤️ My list** — the app *remembers your reviews*: saved places as **Want to
  go** / **Been**, with a star rating and a note, all persisted locally.
  Organise them into **📚 Collections** (named lists like "Date night",
  "Cheap eats"). A **✨ Recommended for you** section suggests similar places
  from what you've saved (Chope-style).
- **Light / dark toggle** (sidebar → *Appearance*) — both keep ChiefEater's
  orange + green accents.
- **🔎 Find food** — search box + region/city filters + a results slider; shows
  a cited Claude answer (when a key is set) or ranked snippet **cards with
  thumbnails** (each post's og:image, with an emoji fallback when a site blocks
  hotlinking), each linking to its source, and each Save-able to your list.
- **➕ Add a source** — paste any **website / article / RSS feed / sitemap URL**
  and ingest it live into the same store. It runs through the *identical* polite
  pipeline (robots.txt + rate limits respected, bot detection never bypassed) and
  is idempotent, so re-adding never duplicates. Tick **“Keep updated daily”** to
  save the feed to `config/user_sources.yaml` so the scheduled refresh keeps
  pulling new posts (see below); the tab lists your saved feeds.

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

The refresh reads **both** `config/sources.yaml` (curated) and
`config/user_sources.yaml` (feeds you add in the app or via
`ingest.add_user_source(...)`), so anything you save with **“Keep updated daily”**
is picked up automatically. Saved feeds default to priority 2, so the
`--min-priority 2` job below includes them.

**macOS (launchd — native, recommended).** A LaunchAgent runs the priority-1&2
refresh daily at 04:30:

```bash
# installed at ~/Library/LaunchAgents/com.autonom.refresh.plist
launchctl load -w ~/Library/LaunchAgents/com.autonom.refresh.plist   # enable
launchctl start com.autonom.refresh                                   # run now (test)
launchctl unload ~/Library/LaunchAgents/com.autonom.refresh.plist     # disable
```

**Linux (cron).** Equivalent nightly line:

```cron
30 4 * * * cd ~/ChiefEpicure && .venv/bin/python ingest.py --min-priority 2 >> ~/ChiefEpicure/ingest.log 2>&1
```

### Daily digest (markdown / email)

`digest.py` builds a "what's new & good" digest (authority picks + freshest
finds) as **Markdown + HTML** into `./digests/YYYY-MM-DD.*`, and a launchd job
(`com.autonom.digest`, 07:00) writes it daily. It's also in the app under
**Today → 📬 Today's digest**.

```bash
python digest.py --days 7            # write ./digests/<date>.md + .html
python digest.py --region MY --email # also email, IF the env vars below are set
```

Email is **off unless** all of these are set (no secrets are ever stored in the
repo; the digest goes from/to your own account):
`DIGEST_SMTP_HOST`, `DIGEST_SMTP_PORT`, `DIGEST_SMTP_USER`, `DIGEST_SMTP_PASS`,
`DIGEST_EMAIL_FROM`, `DIGEST_EMAIL_TO`.

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
  enrich_media.py           # backfill og:image thumbnails onto existing chunks
  personal.py               # home city + saved places/reviews + collections
  digest.py                 # daily "what's new & good" digest (md/html + email)
  util.py                   # shared date parsing + article aggregation
  query.py                  # retrieve (+filters, +--near) -> answer via Claude -> cite
  app.py                    # Streamlit app (Today feed · Find · My list · Add source)
  digests/                  # generated daily digests (git-ignored)
  config/user_sources.yaml  # feeds you add in the app (git-ignored; refreshed daily)
  config/user_data.yaml     # your home city + saved reviews (git-ignored, private)
  requirements.txt
  .env.example
  chroma_db/                # created on first ingest (git-ignored)
```
