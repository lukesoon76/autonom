#!/usr/bin/env python3
"""
Community (user-generated) content helpers: save uploaded photos and index a
member's dining review into the SAME vector store, so ChiefEpicure members'
posts surface in Today / Find / recommendations alongside blogs and Michelin.
"""
import hashlib
import os
import secrets
from collections import Counter
from datetime import datetime, timezone

import auth

# Saved under ./static so Streamlit's static server can serve them (config.toml
# server.enableStaticServing=true). Files in ./static are exposed at URL
# "app/static/…", which works as an <img src> inside the app.
UPLOADS_DIR = os.path.join("static", "uploads")


def save_images(username: str, files) -> list[str]:
    """Persist uploaded images under static/uploads/<user>/; return their paths."""
    if not files:
        return []
    d = os.path.join(UPLOADS_DIR, auth.safe_key(username))
    os.makedirs(d, exist_ok=True)
    paths = []
    for f in files:
        ext = os.path.splitext(f.name)[1].lower() or ".jpg"
        path = os.path.join(d, secrets.token_hex(8) + ext)
        with open(path, "wb") as out:
            out.write(f.getbuffer())
        paths.append(path)
    return paths


def served_url(path_or_url: str) -> str:
    """Map a stored image reference to something a browser <img> can load:
    external http(s) URLs pass through; local static/ paths become app/static/…"""
    if not path_or_url:
        return ""
    if path_or_url.startswith(("http://", "https://", "app/static/")):
        return path_or_url
    p = path_or_url.replace(os.sep, "/")
    return "app/" + p if p.startswith("static/") else ""


def _rid(username: str, review_id: str) -> str:
    return hashlib.sha1(f"community#{username}#{review_id}".encode()).hexdigest()


def source_label(display: str) -> str:
    return f"ChiefEpicure · {display}"


def embed_review(review: dict, username: str, display: str, coll, embedder) -> None:
    """Index (or update) one member review in the shared collection."""
    doc = (f"{review['name']} — {review.get('cuisine', '')} in "
           f"{review.get('city', '')}. {review.get('stars', '')} "
           f"{review.get('text', '')}").strip()
    meta = {
        "source": source_label(display),
        "author": username,
        "community": True,
        "region": review.get("region", ""),
        "city": review.get("city", ""),
        "url": review.get("url", ""),
        "title": review["name"],
        "image": served_url((review.get("images") or [""])[0]),
        "priority": 2,
        "date": review.get("ts", ""),
        "ingested": datetime.now(timezone.utc).isoformat(),
    }
    coll.upsert(ids=[_rid(username, review["id"])], documents=[doc],
                embeddings=embedder.encode([doc]).tolist(), metadatas=[meta])


def unembed_review(username: str, review_id: str, coll) -> None:
    try:
        coll.delete(ids=[_rid(username, review_id)])
    except Exception:
        pass


def featured_contributors(articles: list[dict], top: int = 6) -> list[dict]:
    """Top contributors ('ChiefEpicures') by number of entries, community
    members first, each with a few of their most-recent posts."""
    counts = Counter(a["source"] for a in articles
                     if a.get("source") and a["source"] != "Authority")
    by_src = {}
    for a in articles:
        by_src.setdefault(a["source"], []).append(a)

    def is_member(src):
        return str(src).startswith("ChiefEpicure")

    ranked = sorted(counts, key=lambda s: (not is_member(s), -counts[s]))
    out = []
    for src in ranked[:top]:
        posts = sorted(by_src.get(src, []),
                       key=lambda a: a.get("ts") or datetime.min.replace(
                           tzinfo=timezone.utc), reverse=True)
        out.append({"name": src, "count": counts[src], "member": is_member(src),
                    "posts": posts[:3]})
    return out
