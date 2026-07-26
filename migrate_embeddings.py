#!/usr/bin/env python3
"""
Re-embed the whole corpus with the current EMBED_MODEL.

Run this once after switching models (e.g. to the multilingual model, so Thai /
Vietnamese / Bahasa / etc. retrieve well). It reads every stored document,
re-encodes it, and writes the new vectors back — keeping ids, documents and
metadata intact. If the new model's dimension differs from the collection's, it
transparently rebuilds the collection.

    python migrate_embeddings.py
    python migrate_embeddings.py --batch 128

Safe to re-run. Heavy (encodes the entire corpus locally) — run when idle, not
while a big ingest is writing.
"""
import argparse

import chromadb
from sentence_transformers import SentenceTransformer

from ingest import COLLECTION, DB_PATH, EMBED_MODEL


def run(batch: int):
    print(f"Loading model: {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL)
    new_dim = model.get_sentence_embedding_dimension()
    client = chromadb.PersistentClient(path=DB_PATH)
    coll = client.get_or_create_collection(COLLECTION)

    got = coll.get(include=["documents", "metadatas"])
    ids, docs, metas = got["ids"], got["documents"], got["metadatas"]
    n = len(ids)
    if not n:
        print("Collection is empty — nothing to migrate.")
        return
    print(f"Re-embedding {n} chunks → {new_dim}-dim …")

    # detect current dim (chromadb returns numpy arrays — avoid truthiness)
    peek = coll.peek(1)
    cur = peek.get("embeddings")
    existing_dim = len(cur[0]) if cur is not None and len(cur) else new_dim
    rebuild = existing_dim != new_dim
    if rebuild:
        print(f"Dimension change {existing_dim}→{new_dim}: rebuilding collection.")
        client.delete_collection(COLLECTION)
        coll = client.create_collection(COLLECTION)

    done = 0
    for i in range(0, n, batch):
        j = min(i + batch, n)
        embs = model.encode(docs[i:j], batch_size=batch).tolist()
        if rebuild:
            coll.add(ids=ids[i:j], documents=docs[i:j],
                     embeddings=embs, metadatas=metas[i:j])
        else:
            coll.upsert(ids=ids[i:j], documents=docs[i:j],
                        embeddings=embs, metadatas=metas[i:j])
        done = j
        print(f"  {done}/{n}", end="\r", flush=True)
    print(f"\nDone. Re-embedded {done} chunks with {EMBED_MODEL}. "
          f"Collection holds {coll.count()}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Re-embed the corpus with EMBED_MODEL.")
    ap.add_argument("--batch", type=int, default=128)
    args = ap.parse_args()
    run(args.batch)
