#!/usr/bin/env python3
"""
Facet classification over the corpus metadata — accolade tier, price band and
food-type — derived deterministically from the free-text fields the Eat List
already carries (`accolades`, `price`, `food_type`). No network, no LLM: these
are cheap display/filter helpers, advisory only.

Used by app.py to power the sidebar facet filters and the Map tab.
"""
import re

# ── accolade tier ────────────────────────────────────────────────────────────
ACCOLADE_OPTS = ["Any", "⭐ MICHELIN (any)", "MICHELIN Star", "Bib Gourmand",
                 "MICHELIN Selected"]
_MICHELIN = {"MICHELIN Star", "Bib Gourmand", "MICHELIN Selected"}


def accolade_tier(meta) -> str:
    """Bucket the free-text Accolades field into one tier (or '' if none)."""
    a = (meta.get("accolades") or "").lower()
    if not a.strip():
        return ""
    if "star" in a:
        return "MICHELIN Star"
    if "bib" in a:
        return "Bib Gourmand"
    if "selected" in a or "michelin" in a:
        return "MICHELIN Selected"
    return "Award / notable"


# ── price band ───────────────────────────────────────────────────────────────
PRICE_OPTS = ["Any", "$ Budget", "$$ Mid", "$$$ Upper", "$$$$ Fine dining"]
_RM_TO_SGD = 3.1        # rough normalisation so RM and S$ land in the same bands


def price_band(meta) -> str:
    """Normalise a messy per-pax price string to a 4-band scale (or '')."""
    p = (meta.get("price") or "").strip()
    if not p:
        return ""
    low = p.lower()
    nums = [int(n) for n in re.findall(r"\d+", p)]
    if nums:
        hi = max(nums)
        if "s$" in low or "sgd" in low:
            sgd = hi
        elif "rm" in low:
            sgd = hi / _RM_TO_SGD
        else:
            sgd = hi
        if sgd < 20:
            return "$ Budget"
        if sgd < 60:
            return "$$ Mid"
        if sgd < 200:
            return "$$$ Upper"
        return "$$$$ Fine dining"
    if "budget" in low:
        return "$ Budget"
    if "upper" in low:            # also catches "mid-upper"
        return "$$$ Upper"
    if "mid" in low:
        return "$$ Mid"
    return ""


# ── combined predicate ───────────────────────────────────────────────────────
def passes(meta, acc="Any", price="Any", ft="All") -> bool:
    """True if `meta` satisfies every active facet (a facet at its 'All'/'Any'
    sentinel is inactive)."""
    if acc and acc not in ("Any", ""):
        t = accolade_tier(meta)
        if acc == "⭐ MICHELIN (any)":
            if t not in _MICHELIN:
                return False
        elif t != acc:
            return False
    if price and price not in ("Any", "") and price_band(meta) != price:
        return False
    if ft and ft not in ("All", "") and (meta.get("food_type") or "") != ft:
        return False
    return True
