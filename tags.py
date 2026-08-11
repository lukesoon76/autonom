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


# broad cuisine buckets, à la Chope's guide categorisation
_CUISINE_GROUP = [
    (("omakase", "sushi", "ramen", "izakaya", "japanese", "tempura", "unagi",
      "yakiniku", "donburi", "chirashi"), "Japanese"),
    (("korean",), "Korean"), (("thai", "mookata"), "Thai"),
    (("vietnamese", "pho"), "Vietnamese"),
    (("italian", "pizza", "pasta", "trattoria"), "Italian"),
    (("peranakan", "nyonya"), "Peranakan"),
    (("indian", "mamak", "prata", "nasi kandar"), "Indian"),
    (("malay", "nasi lemak", "nasi "), "Malay"),
    (("cantonese", "sichuan", "teochew", "hainanese", "dim sum", "zi char",
      "taichow", "tai chow", "roast", "chinese", "hokkien", "congee", "claypot",
      "wantan", "duck", "hotpot", "steamboat"), "Chinese"),
    (("french", "european", "spanish", "western", "steak", "grill", "modern",
      "contemporary", "innovative", "british", "american", "mexican"), "Western"),
    (("cafe", "coffee", "brunch", "bakery", "dessert", "gelato", "patisserie",
      "tea"), "Cafe & desserts"),
    (("seafood", "crab", "prawn", "fish"), "Seafood"),
    (("bar", "cocktail", "wine"), "Bars"),
    (("hawker", "kopitiam", "kshf", "noodle", "bak kut teh", "laksa", "char",
      "pan mee", "porridge", "fishball", "beef", "chicken rice", "food centre"),
     "Hawker & local"),
]


def cuisine_group(m) -> str:
    """Broad Chope-style cuisine bucket from food-type/cuisine/title."""
    hay = (f"{m.get('food_type', '')} {m.get('cuisine', '')} "
           f"{m.get('title', '')}").lower()
    for keys, label in _CUISINE_GROUP:
        if any(k in hay for k in keys):
            return label
    return "Other"


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
