#!/usr/bin/env python3
"""
Backfill og:image thumbnails onto already-ingested articles.

New ingests capture og:image automatically (ingest.py). This one-off pass fills
it in for chunks stored before that existed. It re-fetches each article once,
politely (robots.txt + rate limits via ingest.http_get / allowed_by_robots),
pulls the og:image / twitter:image, and writes it to every chunk's `image`
metadata. Idempotent; skips articles that already have an image unless
--overwrite is given.

    python enrich_media.py
    python enrich_media.py --overwrite
"""
import argparse
from collections import defaultdict

import ingest


def run(overwrite: bool):
    coll = ingest.get_collection()
    got = coll.get(include=["metadatas"])
    ids, metas = got["ids"], got["metadatas"]

    idx_by_url = defaultdict(list)
    for i, m in enumerate(metas):
        idx_by_url[m.get("url", "")].append(i)

    n_ok = n_none = n_skip = n_blocked = 0
    upd_ids, upd_meta = [], []

    for url, idxs in idx_by_url.items():
        if not url or metas[idxs[0]].get("source") == "Authority":
            continue
        if not overwrite and metas[idxs[0]].get("image"):
            n_skip += 1
            continue
        if not ingest.allowed_by_robots(url):
            n_blocked += 1
            continue
        html = ingest.http_get(url)
        if not html:
            n_none += 1
            continue
        img = ingest._og_image(html)
        if not img:
            n_none += 1
            continue
        for i in idxs:
            m = dict(metas[i])
            m["image"] = img
            upd_ids.append(ids[i])
            upd_meta.append(m)
        n_ok += 1
        print(f"    🖼  {metas[idxs[0]].get('title','')[:56]}")

    if upd_ids:
        coll.update(ids=upd_ids, metadatas=upd_meta)
    print(f"\nImages: {n_ok} article(s) tagged, {n_none} had none, "
          f"{n_skip} already had one, {n_blocked} robots-blocked. "
          f"Updated {len(upd_ids)} chunks.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backfill og:image thumbnails.")
    ap.add_argument("--overwrite", action="store_true",
                    help="re-fetch even for chunks that already have an image")
    args = ap.parse_args()
    run(args.overwrite)
