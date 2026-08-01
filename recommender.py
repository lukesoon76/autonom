#!/usr/bin/env python3
"""
EatWhatGPT — the recommendation brain behind the Ask tab.

Turns a set of semantic retrieval hits into a *ranked* shortlist with an
explicit, human-readable reason for each pick, working entirely offline
(no API key needed). A Claude summary is layered on top by app.py only when a
key is present — the ranking itself is deterministic and always available.

Signals blended: semantic relevance (dominant) + accolade tier + Google rating
+ optional proximity. A best-effort "open now"is derived from the free-text
hours string; it is advisory and stays silent whenever parsing is uncertain.
"""
import re
from datetime import datetime

import facets

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Asia/Kuala_Lumpur") # MY/SG share UTC+8
except Exception: # pragma: no cover
    _TZ = None

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_ALIAS = {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
          "friday": "fri", "saturday": "sat", "sunday": "sun", "tues": "tue",
          "thurs": "thu", "weds": "wed"}
_ACC_W = {"MICHELIN Star": 1.0, "Bib Gourmand": 0.75, "MICHELIN Selected": 0.55,
          "Award / notable": 0.4}


# ── open-now (best effort, advisory) ─────────────────────────────────────────
def _day_idx(tok: str):
    tok = tok.strip().lower()
    tok = _ALIAS.get(tok, tok)[:3]
    return _DAYS.index(tok) if tok in _DAYS else None


def _closed_days(hours: str) ->set:
    """Weekday indices a place is shut, parsed from '... closed', '(Monday off)'
    etc. Handles single days and 'sun-mon'ranges."""
    out = set()
    low = hours.lower()
    for m in re.finditer(r"([a-z]{3,9})(?:\s*[-–]\s*([a-z]{3,9}))?\s*"
                         r"(?:day|days)?\s*(?:off|closed)|"
                         r"closed\s*(?:on\s*)?([a-z]{3,9})(?:s)?", low):
        a, b, c = m.group(1), m.group(2), m.group(3)
        if c:
            i = _day_idx(c)
            if i is not None:
                out.add(i)
            continue
        ia, ib = _day_idx(a), _day_idx(b) if b else None
        if ia is None:
            continue
        if ib is None:
            out.add(ia)
        else: # inclusive day range, wraps week
            j = ia
            while True:
                out.add(j)
                if j == ib:
                    break
                j = (j + 1) % 7
    return out


def _to_min(h, mm, ap):
    h = int(h)
    mm = int(mm) if mm else 0
    if ap == "pm"and h != 12:
        h += 12
    if ap == "am"and h == 12:
        h = 0
    return h * 60 + mm


def _ranges(hours: str):
    """Extract (open_min, close_min) time windows from a free-text hours string."""
    out = []
    # normalise compact times with no separator: '830am''8:30am', '1130''11:30'
    hours = re.sub(r"\b(\d{1,2})(\d{2})\s*(am|pm)", r"\1:\2\3", hours, flags=re.I)
    pat = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\s*[-–]\s*"
                     r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?", re.I)
    for m in pat.finditer(hours):
        oh, om, oap, ch, cm, cap = m.groups()
        oap = (oap or "").lower()
        cap = (cap or "").lower()
        # infer am/pm when omitted, from the partner marker or common sense
        if not oap and not cap:
            o = int(oh)
            oap = "am"if o < 12 else "pm"
            cap = "pm"# most closings are pm
        elif oap and not cap:
            cap = "pm"if oap == "am"else oap
        elif cap and not oap:
            oap = "am"if cap == "pm"else cap
        o = _to_min(oh, om, oap)
        c = _to_min(ch, cm, cap)
        if c <= o: # e.g. 6pm-1am next day
            c += 24 * 60
        out.append((o, c))
    return out


def open_state(hours: str, now: datetime | None = None):
    """Return 'open'/ 'closed'/ None (unknown) for a free-text hours string."""
    if not hours or not hours.strip():
        return None
    now = now or (datetime.now(_TZ) if _TZ else datetime.now())
    wd, mins = now.weekday(), now.hour * 60 + now.minute
    if wd in _closed_days(hours):
        return "closed"
    ranges = _ranges(hours)
    if not ranges:
        return None
    for o, c in ranges:
        if o <= mins <= c or o <= mins + 24 * 60 <= c:
            return "open"
    return "closed"


# ── rating helper ────────────────────────────────────────────────────────────
def _rating(m) ->float:
    try:
        return float(m.get("rating") or 0)
    except (TypeError, ValueError):
        return 0.0


# ── ranking ──────────────────────────────────────────────────────────────────
def rank(hits, near=None, now=None, limit=8):
    """Blend semantic relevance with accolade + rating (+ proximity) and return
    the top `limit` hits, each annotated with `score` and `reasons`."""
    dists = [h["distance"] for h in hits if h.get("distance") is not None]
    dmin, dmax = (min(dists), max(dists)) if dists else (0.0, 1.0)
    scored = []
    for h in hits:
        m = h["meta"]
        d = h.get("distance")
        sem = 1 - (d - dmin) / (dmax - dmin) if (d is not None and dmax >dmin) else 0.5
        score = 0.58 * sem + 0.24 * _ACC_W.get(facets.accolade_tier(m), 0.0) \
            + 0.18 * min(_rating(m) / 5, 1.0)
        if h.get("distance_km") is not None: # closer is better when geo-ranked
            score += 0.15 * max(0, 1 - h["distance_km"] / 15)
        h = {**h, "score": round(score, 4), "reasons": reasons(h, now)}
        scored.append(h)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def reasons(hit, now=None):
    """Human-readable reason chips for why this place is a good shout."""
    m = hit["meta"]
    out = []
    tier = facets.accolade_tier(m)
    if tier:
        out.append(m.get("accolades") or tier)
    r = _rating(m)
    if r:
        out.append(f"Rated {r:g}")
    band = facets.price_band(m)
    if band:
        out.append(band)
    where = m.get("city") or m.get("region")
    if hit.get("distance_km") is not None:
        out.append(f"{hit['distance_km']:.1f} km away")
    elif where:
        out.append(where)
    state = open_state(m.get("hours", ""), now)
    if state == "open":
        out.append("Open now")
    elif state == "closed":
        out.append("Closed now")
    if m.get("order"):
        out.append(f"Try: {str(m['order'])[:50]}")
    return out
