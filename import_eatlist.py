#!/usr/bin/env python3
"""
Append the Eat List workbook (curated SG / MY / Bangkok eateries) into the
ChiefEpicure corpus as an "Eat List" source, so those hand-verified places show
up in Today / Find / recommendations alongside blogs, Michelin and community
reviews. Embeds with ChiefEpicure's current model; upserts by a stable id so
re-running updates rather than duplicating.

    python import_eatlist.py
    python import_eatlist.py --workbook ~/eatlist/Asia_Eateries_Master_List.xlsx
"""
import argparse
import hashlib
import os
from datetime import datetime, timezone

import openpyxl

import ingest

DEFAULT_WB = os.path.expanduser("~/eatlist/Asia_Eateries_Master_List.xlsx")
COUNTRY_REGION = {"singapore": "SG", "malaysia": "MY", "thailand": "TH"}


def _v(x) -> str:
    s = "" if x is None else str(x).strip()
    return "" if s == "-" else s


def read_rows(wb_path):
    wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=True)
    ml = wb["Master List"]
    it = ml.iter_rows(values_only=True)
    hdr = [str(h).strip() for h in next(it)]
    idx = {h: i for i, h in enumerate(hdr)}

    def g(r, name):
        i = idx.get(name)
        return _v(r[i]) if i is not None and i < len(r) else ""

    for r in it:
        if not r or not _v(r[0]) or not g(r, "Name"):
            continue
        yield {
            "name": g(r, "Name"),
            "country": g(r, "Country"),
            "city": g(r, "City / State"),
            "area": g(r, "Area / Location"),
            "cuisine": g(r, "Cuisine / Style") or g(r, "Food Type Category"),
            "food_type": g(r, "Food Type Category"),
            "address": g(r, "Address"),
            "accolades": g(r, "Accolades"),
            "order": g(r, "What To Order / Signature"),
            "rating": g(r, "Google Rating"),
            "price": g(r, "Price Guide (per pax)"),
            "hours": g(r, "Typical Hours"),
            "tier": g(r, "Source"),
            "url": g(r, "Instagram / Web"),
            "notes": g(r, "Notes"),
        }


def build_doc(r) -> str:
    parts = [f"{r['name']} — {r['cuisine']} in {r['area'] or r['city']}."]
    if r["accolades"]:
        parts.append(r["accolades"] + ".")
    if r["order"]:
        parts.append("Order: " + r["order"] + ".")
    if r["address"]:
        parts.append(r["address"] + ".")
    if r["notes"]:
        parts.append(r["notes"])
    return " ".join(p for p in parts if p)


def stable_id(r) -> str:
    # key on a real URL if present, else name+city — the workbook's
    # "Instagram / Web" field is often just an @handle shared by many venues,
    # so never use it as the dedup key.
    url = (r["url"] or "").strip().lower()
    key = url if url.startswith("http") else f"{r['name']}|{r['city']}".strip().lower()
    return hashlib.sha1(("eatlist#" + key).encode()).hexdigest()


def _rating(s):
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return 0.0


def run(wb_path, batch=256):
    embedder = ingest.get_embedder()
    coll = ingest.get_collection()
    # CORE REBUILD: the curated Master List is the source of truth. Purge every
    # scraped chunk, keeping only Michelin authority and community member
    # reviews; the Eat List rows are then rebuilt fresh below.
    got = coll.get(include=["metadatas"])
    kill = [i for i, m in zip(got["ids"], got["metadatas"])
            if not (m.get("source") == "Authority"
                    or str(m.get("source", "")).startswith("ChiefEpicure"))]
    for k in range(0, len(kill), 500):
        coll.delete(ids=kill[k:k + 500])
    print(f"purged {len(kill)} non-core chunks (scraped sources + old Eat List)")

    now = datetime.now(timezone.utc).isoformat()
    by_id = {}                            # dedupe: last row wins per stable id
    for r in read_rows(wb_path):
        meta = {
            "source": "Eat List",
            "region": COUNTRY_REGION.get(r["country"].lower(), ""),
            "city": r["city"] or r["area"],
            "url": r["url"],
            "title": r["name"],
            "image": "",
            "priority": 1,
            "cuisine": r["cuisine"],
            "food_type": r["food_type"],
            "accolades": r["accolades"],
            "price": r["price"],
            "hours": r["hours"],
            "address": r["address"],
            "order": r["order"],
            "tier": r["tier"],
            "rating": _rating(r["rating"]),
            "date": "",
            "ingested": now,
        }
        by_id[stable_id(r)] = {"doc": build_doc(r), "meta": meta}
    ids = list(by_id)
    docs = [by_id[i]["doc"] for i in ids]
    metas = [by_id[i]["meta"] for i in ids]
    if not ids:
        print("No rows found — check the workbook path / Master List sheet.")
        return
    for i in range(0, len(ids), batch):
        j = min(i + batch, len(ids))
        coll.upsert(ids=ids[i:j], documents=docs[i:j],
                    embeddings=embedder.encode(docs[i:j]).tolist(), metadatas=metas[i:j])
        print(f"  {j}/{len(ids)}", end="\r", flush=True)
    print(f"\nAppended {len(ids)} Eat List entries. Collection now holds {coll.count()}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Append the Eat List workbook into ChiefEpicure.")
    ap.add_argument("--workbook", default=DEFAULT_WB)
    ap.add_argument("--batch", type=int, default=256)
    args = ap.parse_args()
    run(os.path.expanduser(args.workbook), args.batch)
