#!/usr/bin/env python3
"""
Personal layer for ChiefEpicure — your home city + memory of your own reviews.

Chope-style: the app remembers the places you save, mark as *been*, rate, and
note, and uses them to personalise recommendations. Stored locally in
config/user_data.yaml (git-ignored — it's your private data):

    prefs:  {region, city, lat, lng}
    places: [{url, title, source, region, city, image, status, rating, note, ts}]

status ∈ {"want" (want to go), "been" (visited)}.
"""
import os

import yaml

DATA_PATH = "config/user_data.yaml"


def _load() -> dict:
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w") as f:
        f.write("# ChiefEpicure personal data — your home city and saved places /\n"
                "# reviews. Private & local; safe to hand-edit.\n")
        yaml.safe_dump(d, f, sort_keys=False, allow_unicode=True)


# ── home-city preferences ────────────────────────────────────────────────────
def get_prefs() -> dict:
    return _load().get("prefs", {}) or {}


def set_prefs(**kw) -> dict:
    d = _load()
    prefs = d.setdefault("prefs", {})
    prefs.update({k: v for k, v in kw.items() if v is not None})
    _save(d)
    return prefs


# ── saved places / reviews ───────────────────────────────────────────────────
def load_places() -> list[dict]:
    return _load().get("places", []) or []


def saved_urls() -> set:
    return {p.get("url") for p in load_places()}


def get_place(url: str):
    return next((p for p in load_places() if p.get("url") == url), None)


def upsert_place(url: str, *, ts: str = "", **fields) -> dict:
    """Add or update a saved place. Only provided fields overwrite existing ones."""
    d = _load()
    places = d.setdefault("places", [])
    existing = next((p for p in places if p.get("url") == url), None)
    if existing is None:
        entry = {"url": url, "status": "want", "rating": 0, "note": "", "ts": ts}
        entry.update(fields)
        places.append(entry)
    else:
        existing.update(fields)
        entry = existing
    _save(d)
    return entry


def remove_place(url: str) -> None:
    d = _load()
    d["places"] = [p for p in d.get("places", []) if p.get("url") != url]
    for name in list(d.get("collections", {})):        # drop from any collection
        d["collections"][name] = [u for u in d["collections"][name] if u != url]
    _save(d)


# ── collections (named lists, Chope-style) ───────────────────────────────────
def load_collections() -> dict:
    return _load().get("collections", {}) or {}


def create_collection(name: str) -> None:
    name = name.strip()
    if not name:
        return
    d = _load()
    d.setdefault("collections", {}).setdefault(name, [])
    _save(d)


def delete_collection(name: str) -> None:
    d = _load()
    d.get("collections", {}).pop(name, None)
    _save(d)


def set_collections_for(url: str, names: list[str]) -> None:
    """Make `url` belong to exactly the given collections (creating as needed)."""
    d = _load()
    cols = d.setdefault("collections", {})
    for n in list(cols):
        cols[n] = [u for u in cols[n] if u != url]
    for n in names:
        cols.setdefault(n, [])
        if url not in cols[n]:
            cols[n].append(url)
    _save(d)


def collections_for(url: str) -> list[str]:
    return [n for n, urls in load_collections().items() if url in urls]
