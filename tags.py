#!/usr/bin/env python3
"""
Display tags derived from existing metadata — district, setting, meal window —
so entry cards can show rich, foodie-blog-style chips without re-embedding.
Cheap and deterministic; computed at render time.
"""
import geo_gazetteer
import recommender

_SETTING = [("omakase", "Omakase"), ("kopitiam", "Kopitiam"),
            ("kshf", "Hawker"), ("hawker", "Hawker"), ("fine dining", "Fine dining"),
            ("cafe", "Cafe"), ("brunch", "Cafe"), ("cocktail", "Bar"),
            ("bar", "Bar"), ("seafood", "Seafood"), ("zi char", "Zi char"),
            ("tai chow", "Zi char"), ("taichow", "Zi char"),
            ("bak kut teh", "Hawker"), ("noodle", "Noodles")]


def district(m) -> str:
    """Neighbourhood, matched against the curated gazetteer district list."""
    hay = " ".join(str(m.get(k, "") or "") for k in
                   ("address", "city", "title")).lower()
    for key in geo_gazetteer._AREA_KEYS:
        if key in hay:
            return key.title()
    return ""


def setting(m) -> str:
    """Venue setting (Hawker / Cafe / Fine dining / …) from the food-type."""
    hay = f"{m.get('food_type', '')} {m.get('cuisine', '')}".lower()
    for k, v in _SETTING:
        if k in hay:
            return v
    return ""


def meal(m) -> str:
    """Rough meal window from the hours string (advisory)."""
    ranges = recommender._ranges(m.get("hours", "") or "")
    if not ranges:
        return ""
    opens = min(o for o, _ in ranges)
    closes = max(c for _, c in ranges)
    if closes >= 23 * 60 or closes > 24 * 60:
        return "Supper"
    if opens <= 9 * 60:
        return "Breakfast"
    if opens >= 17 * 60:
        return "Dinner"
    return "Lunch"
