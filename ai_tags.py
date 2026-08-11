#!/usr/bin/env python3
"""
GenAI tag enrichment — fills the gaps deterministic rules can't (cuisine, dishes,
setting, district) so no entry card is left with empty tags.

Design: results are CACHED to config/ai_tags.json (persists on the Render disk),
so the app reads tags instantly with NO per-render LLM call. `get(meta)` returns
the cached dict (or {} if not yet generated). `warm(n)` pre-fills the cache for
entries that are missing key fields — run it as a one-off / scheduled job.

Guardrails: only OBJECTIVE descriptive tags are inferred. It never fabricates
accolades, ratings or halal/dietary status — those must be real, so uncertain
entries surface a "Verify" prompt in the UI instead.
"""
import argparse
import hashlib
import json
import os
import time

import ingest
import query

CACHE = os.path.join("config", "ai_tags.json")
SYSTEM = (
    "You tag Malaysian & Singaporean eateries for a food guide. From the name and "
    "address ONLY, return STRICT JSON with keys: cuisine (short, e.g. 'Teochew', "
    "'Char kuey teow', 'Japanese omakase'), dishes (array of up to 3 likely dishes), "
    "setting (one of: Hawker, Kopitiam, Cafe, Casual, Fine dining, Bar, Zi char, "
    "Omakase, Bakery), district (neighbourhood). Base it only on obvious cues; use an "
    "empty string/array if genuinely unsure. NEVER invent awards, ratings, prices or "
    "halal/dietary status. Output JSON only, no prose.")


def _key(m) -> str:
    return hashlib.sha1((f"{m.get('title', '')}|{m.get('city', '')}").encode()).hexdigest()


def _load() -> dict:
    try:
        return json.load(open(CACHE))
    except (ValueError, OSError):
        return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(d, open(CACHE, "w"), ensure_ascii=False)


def get(m) -> dict:
    """Cached AI tags for an entry (no API call). {} if not generated yet."""
    return _load().get(_key(m), {})


def generate(m, cache=None) -> dict:
    """Call the LLM once for an entry, cache and return {cuisine,dishes,setting,district}."""
    if not query.has_api_key():
        return {}
    q = f"Name: {m.get('title', '')}\nAddress: {m.get('address', '') or m.get('city', '')}"
    try:
        # cheap model — tag extraction doesn't need Opus, and we run it at scale
        raw = query.answer(q, "", system=SYSTEM,
                           model="claude-haiku-4-5-20251001") or ""
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        tags = json.loads(raw)
    except Exception:
        return {}
    keep = {k: tags.get(k, "") for k in ("cuisine", "setting", "district")}
    keep["dishes"] = tags.get("dishes", [])[:3] if isinstance(tags.get("dishes"), list) else []
    d = cache if cache is not None else _load()
    d[_key(m)] = keep
    if cache is None:
        _save(d)
    return keep


def warm(limit: int) -> int:
    """Pre-fill the cache for up to `limit` entries missing cuisine/food_type."""
    if not query.has_api_key():
        print("No ANTHROPIC_API_KEY — cannot warm the AI tag cache.")
        return 0
    metas = ingest.get_collection().get(include=["metadatas"]).get("metadatas", []) or []
    cache = _load()
    done = 0
    for m in metas:
        if done >= limit:
            break
        if _key(m) in cache:
            continue
        if (m.get("cuisine") or "").strip() and (m.get("food_type") or "").strip():
            continue                              # already well-tagged
        generate(m, cache=cache)
        done += 1
        if done % 20 == 0:
            _save(cache)
            print(f"  warmed {done}…")
        time.sleep(0.4)
    _save(cache)
    print(f"Warmed {done} entries → {CACHE}")
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Warm the GenAI tag cache.")
    ap.add_argument("--warm", type=int, default=100, help="max entries to enrich")
    args = ap.parse_args()
    warm(args.warm)
