#!/usr/bin/env python3
"""
FoodRAG authority curation.

Michelin and Asia's 50 Best are *never scraped* — their lists are curated by
hand into config/curated_authority.csv (name, city, region, stars, cuisine,
url, note). This script embeds one short document per row and upserts it into
the SAME `food_reviews` collection used by ingest.py, so authoritative picks
surface alongside blog reviews in query.py.

Usage:
    python curate_authority.py
    python curate_authority.py --csv config/curated_authority.csv

Re-running is safe: ids are content-stable (sha1 of "authority#"+url), so rows
are updated in place rather than duplicated.
"""
import argparse
import csv
import hashlib
import sys

import chromadb
from sentence_transformers import SentenceTransformer

EMBED_MODEL = "all-MiniLM-L6-v2"
DB_PATH = "./chroma_db"
COLLECTION = "food_reviews"
DEFAULT_CSV = "config/curated_authority.csv"


def stable_id(url: str) -> str:
    return hashlib.sha1(f"authority#{url}".encode()).hexdigest()


def build_document(row: dict) -> str:
    """One compact, embeddable line per authoritative place."""
    parts = [f"{row['name']} — {row.get('cuisine', '').strip()} in {row['city']}."]
    if row.get("stars", "").strip():
        parts.append(f"{row['stars'].strip()}.")
    if row.get("note", "").strip():
        parts.append(row["note"].strip())
    return " ".join(p for p in parts if p)


def run(csv_path: str) -> None:
    embedder = SentenceTransformer(EMBED_MODEL)
    client = chromadb.PersistentClient(path=DB_PATH)
    coll = client.get_or_create_collection(COLLECTION)

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("name", "").strip()]

    if not rows:
        print(f"No rows found in {csv_path} — nothing to curate.")
        return

    ids, docs, metas = [], [], []
    for row in rows:
        url = row.get("url", "").strip()
        if not url:
            print(f"    skipping '{row.get('name')}' — no url (used for stable id)")
            continue
        ids.append(stable_id(url))
        docs.append(build_document(row))
        metas.append({
            "source": "Authority",
            "region": row.get("region", "").strip(),
            "city": row.get("city", "").strip(),
            "url": url,
            "title": row["name"].strip(),
            "priority": 1,
        })
        print(f"    + {row['name'].strip()}  ({row.get('stars', '').strip() or 'listed'})")

    if not ids:
        print("No rows had a url — nothing upserted.")
        return

    embeddings = embedder.encode(docs).tolist()
    coll.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
    print(f"\nDone. Curated {len(ids)} authority rows. "
          f"Collection now holds {coll.count()} chunks at {DB_PATH}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Import curated Michelin/50 Best rows.")
    ap.add_argument("--csv", default=DEFAULT_CSV, help="path to curated CSV")
    args = ap.parse_args()
    try:
        run(args.csv)
    except FileNotFoundError:
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
