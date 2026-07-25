#!/usr/bin/env python3
"""
ChiefEpicure — Streamlit UI for the FoodRAG "where to eat" system.

    streamlit run app.py

A friendly front-end over the same local pipeline used by the CLI:
  • Find food  — semantic search over ingested MY/SG reviews, with a cited
                 Claude answer (when ANTHROPIC_API_KEY is set) or ranked
                 snippet cards otherwise. Every result links to its source.
  • Add a source — paste any website / article / feed / sitemap URL and ingest
                 it live through the SAME polite pipeline (robots.txt + rate
                 limits respected; nothing bypasses bot detection).
"""
import os
from collections import Counter

import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import ingest
import query

st.set_page_config(page_title="ChiefEpicure", page_icon="🍜", layout="wide")

REGION_LABEL = {"MY": "🇲🇾 Malaysia", "SG": "🇸🇬 Singapore",
                "MY_SG": "🌏 MY & SG", "": "—"}
EXAMPLES = [
    "good char kway teow in KL",
    "chilli crab tonight",
    "natural wine bars in Singapore",
    "new omakase worth booking",
    "best brunch cafes",
    "Michelin fine dining tasting menu",
]


# ── cached resources ─────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="Loading embedding model…")
def _embedder():
    return ingest.get_embedder()


@st.cache_resource(show_spinner="Opening vector store…")
def _collection():
    return ingest.get_collection()


def db_stats(coll):
    """Aggregate counts by source / region for the sidebar (cheap for our size)."""
    try:
        got = coll.get(include=["metadatas"])
    except Exception:
        return {"total": 0, "sources": Counter(), "regions": Counter()}
    metas = got.get("metadatas", []) or []
    return {
        "total": len(metas),
        "sources": Counter(m.get("source", "?") for m in metas),
        "regions": Counter(m.get("region", "?") for m in metas),
    }


# ── styling ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.card{border:1px solid #2c2f36;border-radius:12px;padding:14px 16px;margin-bottom:12px;
      background:#1418200d;}
.card h4{margin:0 0 4px 0;font-size:1.02rem;}
.badge{display:inline-block;font-size:.72rem;padding:2px 8px;border-radius:999px;
       background:#2b3340;color:#cdd6e2;margin-right:6px;}
.snippet{color:#9aa4b2;font-size:.9rem;line-height:1.45;margin:6px 0;}
.src a{font-size:.82rem;color:#5aa0ff;text-decoration:none;}
.answer{border-left:3px solid #ff7a59;padding:6px 0 6px 16px;}
</style>
""", unsafe_allow_html=True)


# ── header ───────────────────────────────────────────────────────────────────
st.title("🍜 ChiefEpicure")
st.caption("Where to eat in **Malaysia & Singapore** — grounded in food-blog "
           "reviews you've ingested, always with sources.")

coll = _collection()
stats = db_stats(coll)

with st.sidebar:
    st.subheader("Corpus")
    if stats["total"] == 0:
        st.warning("The vector store is empty. Ingest first:\n\n"
                   "`python ingest.py --region SG --limit 5`", icon="📭")
    else:
        st.metric("Chunks indexed", f"{stats['total']:,}")
        reg = "  ·  ".join(f"{REGION_LABEL.get(r, r)} {n}"
                           for r, n in stats["regions"].most_common())
        st.caption(reg)
        with st.expander(f"{len(stats['sources'])} sources"):
            for name, n in stats["sources"].most_common():
                st.write(f"• {name} — {n}")

    st.divider()
    st.subheader("Filters")
    region = st.radio("Region", ["All", "MY", "SG"], horizontal=True,
                      format_func=lambda r: REGION_LABEL.get(r, r) if r != "All" else "All")
    city = st.text_input("City contains", placeholder="e.g. Kuala Lumpur / Singapore")
    k = st.slider("Results (k)", 3, 12, 6)

    with st.expander("📍 Near a point (geo)"):
        st.caption("Rank by distance to a lat,lng. Run `enrich_geo.py` first so "
                   "places carry coordinates.")
        near_str = st.text_input("lat, lng", placeholder="3.1390, 101.6869")
        radius_km = st.slider("Radius (km)", 1, 50, 10)

    st.divider()
    if query.has_api_key():
        st.success("Claude answers: **on** (API key found)", icon="✅")
    else:
        st.info("No API key — showing ranked snippets.\nSet `ANTHROPIC_API_KEY` "
                "in `.env` for written answers.", icon="💡")

find_tab, add_tab = st.tabs(["🔎  Find food", "➕  Add a source"])


# ── tab 1: find food ─────────────────────────────────────────────────────────
def render_hit(h, i):
    m = h["meta"]
    title = m.get("title") or m.get("url", "")
    dist = (f"<span class='badge' style='background:#3a2b1a;color:#ffb37a'>"
            f"📍 {h['distance_km']:.1f} km</span>") if "distance_km" in h else ""
    badges = (f"{dist}"
              f"<span class='badge'>{REGION_LABEL.get(m.get('region',''), m.get('region',''))}</span>"
              f"<span class='badge'>{m.get('city','')}</span>"
              f"<span class='badge'>{m.get('source','')}</span>")
    snippet = (h["doc"][:320] + "…") if len(h["doc"]) > 320 else h["doc"]
    st.markdown(
        f"<div class='card'><h4>{i}. {title}</h4>{badges}"
        f"<div class='snippet'>{snippet}</div>"
        f"<div class='src'>🔗 <a href='{m.get('url','')}' target='_blank'>{m.get('url','')}</a></div></div>",
        unsafe_allow_html=True,
    )


with find_tab:
    if "q" not in st.session_state:
        st.session_state.q = ""
    st.write("**Try:**")
    cols = st.columns(len(EXAMPLES))
    for c, ex in zip(cols, EXAMPLES):
        if c.button(ex, use_container_width=True):
            st.session_state.q = ex

    q = st.text_input("What are you in the mood for?",
                      value=st.session_state.q,
                      placeholder="where's good laksa in KL?")
    go = st.button("Find", type="primary")

    if go and q.strip():
        reg = None if region == "All" else region
        cty = city.strip() or None
        near = None
        if near_str.strip():
            try:
                a, b = near_str.split(",")
                near = (float(a), float(b))
            except ValueError:
                st.warning('“Near” must look like `3.1390, 101.6869` — ignoring it.')
        with st.spinner("Searching the corpus…"):
            hits = query.retrieve(q, k=k, region=reg, city=cty,
                                  embedder=_embedder(), coll=coll,
                                  near=near, radius_km=radius_km if near else None)
        if near and not hits:
            st.info("No geo-tagged places within that radius. Widen the radius, or "
                    "run `enrich_geo.py` to add coordinates.", icon="📍")
        if not hits:
            st.warning("No matches — try widening the filters or ingesting more sources.")
        else:
            if query.has_api_key():
                context, _ = query.build_context(hits)
                reply = None
                try:
                    with st.spinner("Asking Claude (grounded only in these snippets)…"):
                        reply = query.answer(q, context)
                except Exception as e:
                    st.warning(f"Couldn't generate a written answer ({type(e).__name__}: "
                               f"{e}). Showing ranked snippets instead.", icon="⚠️")
                if reply:
                    st.markdown("### Recommendation")
                    st.markdown(f"<div class='answer'>{reply}</div>", unsafe_allow_html=True)
                    st.divider()
            st.markdown(f"### {len(hits)} sources")
            for i, h in enumerate(hits, 1):
                render_hit(h, i)

            seen, lines = set(), []
            for h in hits:
                u = h["meta"].get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    lines.append(f"- [{h['meta'].get('source','')}]({u})")
            with st.expander("Sources (de-duplicated)"):
                st.markdown("\n".join(lines))


# ── tab 2: add a source ──────────────────────────────────────────────────────
with add_tab:
    st.write("Ingest **your own** website, article, RSS feed, or sitemap into the "
             "same store. It goes through the identical polite pipeline — "
             "**robots.txt and rate limits are respected**, and bot detection is "
             "never bypassed.")

    with st.form("add_source"):
        url = st.text_input("URL", placeholder="https://example.com/best-nasi-lemak/")
        c1, c2, c3 = st.columns(3)
        kind = c1.selectbox("Type", ["auto", "page", "rss", "sitemap"],
                            help="auto = sniff from the URL. page = one article. "
                                 "rss = a feed. sitemap = an XML sitemap.")
        reg2 = c2.selectbox("Region", ["MY", "SG", ""], index=0)
        city2 = c3.text_input("City", placeholder="Kuala Lumpur")
        c4, c5 = st.columns(2)
        label = c4.text_input("Source label", value="User URL")
        url_filter = c5.text_input("Sitemap URL filter (optional)",
                                   placeholder="/restaurants/")
        limit = st.slider("Max articles (feeds/sitemaps)", 1, 30, 10)
        submitted = st.form_submit_button("Ingest", type="primary")

    if submitted and url.strip():
        with st.spinner(f"Fetching politely (≥{ingest.REQUEST_DELAY}s/host)…"):
            out = ingest.ingest_user_source(
                url.strip(), kind=kind, region=reg2, city=city2.strip(),
                source=label.strip() or "User URL", url_filter=url_filter.strip(),
                limit=limit, embedder=_embedder(), coll=coll)
        added = out["added_chunks"]
        n_ok = sum(1 for r in out["results"] if r["status"] == "ok")
        n_blocked = sum(1 for r in out["results"] if r["status"] == "blocked")
        if added:
            st.success(f"Ingested **{n_ok}** article(s) → **{added}** chunks "
                       f"(resolved as `{out['kind']}`). Re-running is idempotent.")
        else:
            st.warning(f"No new content ingested (resolved as `{out['kind']}`).")
        if n_blocked:
            st.info(f"{n_blocked} URL(s) skipped — disallowed by robots.txt.", icon="🤖")
        with st.expander("Per-URL detail"):
            for r in out["results"]:
                icon = {"ok": "✅", "blocked": "🤖", "fetch_failed": "⚠️",
                        "too_short": "📄", "no_url": "❔"}.get(r["status"], "•")
                st.write(f"{icon} `{r['status']}` — {r.get('title') or r['url']} "
                         f"({r['chunks']} chunks)")
        st.caption("Switch to **Find food** — your new content is searchable now.")
