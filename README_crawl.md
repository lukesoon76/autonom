# Food-blog crawl toolchain

Two scripts that extend `Asia_Eateries_Master_List.xlsx` from Singapore and
Malaysia food blogs. Run them on your own machine — they need outbound internet.

## Setup

    pip install requests beautifulsoup4 lxml openpyxl

Put both scripts and the workbook in one folder.

## 1. Crawl

    python crawl_food_blogs.py --list                       # see the site registry
    python crawl_food_blogs.py --site eatdrinkkl --max-pages 40
    python crawl_food_blogs.py --all --max-pages 25         # first sweep of everything

Instead of scraping HTML it asks each site's API for clean JSON:

| Platform  | Endpoint                                        | Sites |
|-----------|-------------------------------------------------|-------|
| WordPress | `/wp-json/wp/v2/posts?per_page=100&page=N`       | SethLui, Eatbook, Miss Tam Chiak, ieatishootipost, Daniel Food Diary, Lady Iron Chef, VKeong, KY Speaks, Bangsar Babe, Chiefeater |
| Blogger   | `/feeds/posts/default?alt=json&start-index=N`    | Eat Drink KL, Johor Kaki |

Per post it extracts name, country, address, phone, hours, price and a
closed-down flag, then writes to `crawl_output/<site>.csv`.

**Resumable.** Each site keeps a `.state` marker. Re-run the same command and it
continues from where it stopped; Ctrl-C is safe. Already-seen URLs are skipped,
so nothing is duplicated across runs.

**Polite by default:** obeys `robots.txt`, 1.5s between requests, backs off on
429/5xx, identifies itself in the User-Agent. Don't drop `DELAY` below ~1s and
don't run several sites against the same host at once.

Posts are discarded when they have neither an address nor a phone number, or
when the title looks like a round-up, recipe, giveaway or non-food listicle.

## 2. Merge

    python merge_into_master.py --workbook Asia_Eateries_Master_List.xlsx \
                                --csv "crawl_output/*.csv"

    # inspect first, change nothing:
    python merge_into_master.py --workbook Asia_Eateries_Master_List.xlsx \
                                --csv "crawl_output/*.csv" --dry-run

    # write somewhere else so you can diff:
    ... --out Asia_Eateries_v2.xlsx

It de-duplicates on (country, normalised name) against every row already in the
workbook, maps each venue into your food-type taxonomy from dish keywords,
drops anything flagged closed, adds the rows as a new colour tier, and rebuilds
the Master List plus all 23 category tabs and the Index.

Open the result in Excel once so the count formulas calculate.

## Tuning

- New site: add a line to `SITES` in the crawler.
- Wrong category: edit `RULES_MY` / `RULES_SG` in the merge script. First match
  wins, so specific patterns go above generic ones.
- Too aggressive a filter: relax `SKIP_TITLE`, or the address/phone gate in
  `crawl()`.

## Expected yield

Eat Drink KL and SethLui's Top 300 Hawkers are the two densest sources and
should roughly double the workbook on their own. Start with `--max-pages 10`
on a single site to sanity-check the output before running the full sweep.
