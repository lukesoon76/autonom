#!/usr/bin/env python3
"""
FoodRAG authority curation.

Michelin and Asia's 50 Best are *never scraped* — their lists are curated by
hand (from the printed guide or the official site, as **facts only**: name,
city, stars, cuisine, address/url — never the guide's review prose) into
config/curated_authority.csv. This script embeds one short document per row and
upserts it into the SAME `food_reviews` collection used by ingest.py, so
authoritative picks surface alongside blog reviews.

Usage:
    python curate_authority.py
    python curate_authority.py --csv config/curated_authority.csv

Re-running is safe: ids are content-stable, so rows are updated, not duplicated.
The helpers here (add_row / append_csv) are also used by the app's
"Add a Michelin / authority pick" form.
"""
import argparse
import csv
import hashlib
import os
import sys

import chromadb
from sentence_transformers import SentenceTransformer

EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"  # 384-dim, ASEAN languages
DB_PATH = "./chroma_db"
COLLECTION = "food_reviews"
DEFAULT_CSV = "config/curated_authority.csv"
CSV_FIELDS = ["name", "city", "region", "stars", "cuisine", "url", "note"]


def get_embedder() -> SentenceTransformer:
    return SentenceTransformer(EMBED_MODEL)


def get_collection():
    return chromadb.PersistentClient(path=DB_PATH).get_or_create_collection(COLLECTION)


def _key(row: dict) -> str:
    """Stable identity — prefer the URL, else name+city (book entries may have
    no URL)."""
    return (row.get("url") or "").strip() or \
        f"{row.get('name', '').strip()}|{row.get('city', '').strip()}"


def stable_id(row_or_url) -> str:
    key = row_or_url if isinstance(row_or_url, str) else _key(row_or_url)
    return hashlib.sha1(f"authority#{key}".encode()).hexdigest()


def build_document(row: dict) -> str:
    """One compact, embeddable line per authoritative place."""
    parts = [f"{row['name']} — {row.get('cuisine', '').strip()} in {row.get('city', '')}."]
    if (row.get("stars") or "").strip():
        parts.append(f"{row['stars'].strip()}.")
    if (row.get("note") or "").strip():
        parts.append(row["note"].strip())
    return " ".join(p for p in parts if p)


def _meta(row: dict) -> dict:
    return {
        "source": "Authority",
        "region": (row.get("region") or "").strip(),
        "city": (row.get("city") or "").strip(),
        "url": (row.get("url") or "").strip(),
        "title": row["name"].strip(),
        "image": (row.get("image") or "").strip(),
        "priority": 1,
    }


def add_rows(rows: list[dict], embedder=None, coll=None) -> int:
    """Embed + upsert authority rows (each needs at least a name). Returns count."""
    rows = [r for r in rows if (r.get("name") or "").strip()]
    if not rows:
        return 0
    embedder = embedder or get_embedder()
    coll = coll or get_collection()
    ids = [stable_id(r) for r in rows]
    docs = [build_document(r) for r in rows]
    metas = [_meta(r) for r in rows]
    coll.upsert(ids=ids, documents=docs,
                embeddings=embedder.encode(docs).tolist(), metadatas=metas)
    return len(rows)


def append_csv(row: dict, path: str = DEFAULT_CSV) -> None:
    """Append one row to the curated CSV (creating it with a header if needed),
    de-duplicating by name+city."""
    existing = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    key = (row.get("name", "").strip().lower(), row.get("city", "").strip().lower())
    existing = [r for r in existing
                if (r.get("name", "").strip().lower(),
                    r.get("city", "").strip().lower()) != key]
    existing.append({k: (row.get(k) or "").strip() for k in CSV_FIELDS})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(existing)


def run(csv_path: str) -> None:
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("name", "").strip()]
    if not rows:
        print(f"No rows found in {csv_path} — nothing to curate.")
        return
    coll = get_collection()
    n = add_rows(rows, coll=coll)
    for r in rows:
        print(f"    + {r['name'].strip()}  ({(r.get('stars') or '').strip() or 'listed'})")
    print(f"\nDone. Curated {n} authority rows. "
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
