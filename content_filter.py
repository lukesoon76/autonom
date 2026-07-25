#!/usr/bin/env python3
"""
Sponsored / PR content detection.

The MY/SG food feeds mix genuine reviews with brand PR, advertorials, and
corporate press releases ("Samsung Unveils…", "…Brings Back Campaign"). This
module flags those so ingest.py can skip them and a maintenance pass can prune
any already in the store.

Design: **precision-first**. We would rather keep a borderline post than nuke a
real review, so we only flag on strong signals — explicit sponsorship
disclosures in the body, or unambiguous corporate-announcement titles.
"""
import re

# ── strong body disclosures (high precision) ─────────────────────────────────
# Substring match, case-insensitive. Kept deliberately unambiguous — "invited"
# / "in collaboration with" are intentionally EXCLUDED (they appear in genuine
# reviews of collab dinners and media tastings).
DISCLOSURE_PHRASES = (
    "sponsored post",
    "this post is sponsored",
    "this is a sponsored",
    "sponsored by",
    "paid partnership",
    "paid post",
    "advertorial",
    "#ad",
    "# ad",
    "press release",
    "brought to you by",
    "in paid partnership",
)

# ── corporate-announcement title patterns ────────────────────────────────────
# These verbs, in a TITLE, are overwhelmingly product/brand PR rather than food
# reviews. Applied to the title only (bodies use the disclosure list above).
TITLE_PR_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\bunveil(s|ed|ing)?\b",
    r"\bredefin(e|es|ing)\b",
    r"\bdebuts?\b",
    r"\bpartner experience\b",
    r"\bpress release\b",
    r"\bcampaign\b",
    r"\bgift set\b",
    r"\bmooncake .*(gift|set|collection)\b",
    # unambiguous consumer-tech / product PR nouns (never food-review titles)
    r"\beyewear\b",
    r"\bfoldable\b",
    r"\bespresso machine\b",
    r"\bgalaxy (ecosystem|z ?(flip|fold)|s\d{2})\b",
    r"\b(launch(es|ed)?|introduc(es|ed|ing)|brings?) (the |its |a new |new |back )?"
    r"(all-new |new )?(galaxy|ecosystem|espresso|machine|eyewear|"
    r"store|residence|collection|line-?up|partner)\b",
))


def looks_sponsored(title: str, text: str) -> tuple[bool, str]:
    """Return (is_sponsored, reason). reason is '' when not flagged."""
    t = (title or "").strip()
    body = (text or "")
    low = body.lower()

    for phrase in DISCLOSURE_PHRASES:
        if phrase in low:
            return True, f"disclosure:{phrase!r}"

    for pat in TITLE_PR_PATTERNS:
        if pat.search(t):
            return True, f"title:/{pat.pattern}/"

    return False, ""
