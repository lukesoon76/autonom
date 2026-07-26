#!/usr/bin/env python3
"""
Shared helpers used by both the Streamlit app and the headless digest:
date parsing and article aggregation (one record per URL, newest first).
Kept Streamlit-free so digest.py can run under cron/launchd.
"""
import datetime as dt
from email.utils import parsedate_to_datetime

import ingest


def parse_pub(s: str):
    """Parse a feed date string to a tz-aware datetime, or None."""
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)          # RFC-822 (most RSS)
        if d:
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:
        pass
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))  # ISO
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def ago(d, now=None) -> str:
    """Human 'time ago' label for a datetime (or '' if unknown)."""
    if not d:
        return ""
    now = now or dt.datetime.now(dt.timezone.utc)
    days = (now - d).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return d.strftime("%b %Y")


def load_articles(coll) -> list[dict]:
    """One record per article (deduped by URL), newest first."""
    got = coll.get(include=["metadatas", "documents"])
    ids, metas, docs = got["ids"], got["metadatas"], got["documents"]
    arts = {}
    for i, m in enumerate(metas):
        u = m.get("url", "")
        if not u:
            continue
        first = ids[i] == ingest.stable_id(u, 0)   # opening chunk → best excerpt
        a = arts.get(u)
        if a is None:
            arts[u] = {"url": u, "title": m.get("title", "") or u,
                       "source": m.get("source", ""), "region": m.get("region", ""),
                       "city": m.get("city", ""), "image": m.get("image", ""),
                       "date": m.get("date", ""), "priority": m.get("priority", 99),
                       "text": docs[i], "_first": first}
        elif first:
            a["text"], a["_first"] = docs[i], True
    out = []
    floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    for a in arts.values():
        a["ts"] = parse_pub(a["date"])
        out.append(a)
    out.sort(key=lambda a: (a["ts"] or floor), reverse=True)
    return out
