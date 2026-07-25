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


# ── theme — ChiefEater palette (orange + green), in light and dark ────────────
# Accents (orange/green) stay constant; only surfaces/text flip between modes.
THEME = {
    "Light": dict(bg="#ffffff", sidebar="#f2f3f5", card="#ffffff", panel="#ffffff",
                  ink="#252525", muted="#5b6470", border="#ededed", thumb="#f2f3f5",
                  orange="#fa8b0c", orange_d="#d9760a", green="#28a800",
                  warn_bg="#fff4e6", warn_bd="#ffe0b8",
                  reg_bg="#e8f7e3", reg_fg="#1f7a00", geo_bg="#fff1de",
                  ans_bg="#f7fbf5", chip="#f2f3f5"),
    "Dark": dict(bg="#14171c", sidebar="#1b1f27", card="#1b1f27", panel="#20252e",
                 ink="#eef1f5", muted="#9aa4b2", border="#2b3240", thumb="#2b3240",
                 orange="#ff9e2c", orange_d="#ffb455", green="#5ec53b",
                 warn_bg="#2a2113", warn_bd="#5a4420",
                 reg_bg="#12331a", reg_fg="#7ddc5b", geo_bg="#33260f",
                 ans_bg="#16241a", chip="#20252e"),
}


def build_css(p: dict) -> str:
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Abril+Fatface&family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400&family=Oxygen:wght@400;700&display=swap');

html, body, [class*="css"], .stMarkdown, p, div, span, label, input, textarea, button {{
    font-family: 'Oxygen', -apple-system, sans-serif;
}}
h1, h2, h3, .brand {{ font-family: 'Abril Fatface', Georgia, serif !important; }}

/* surfaces (drive the light/dark flip) */
.stApp, [data-testid="stHeader"] {{ background: {p['bg']}; }}
[data-testid="stSidebar"] {{ background: {p['sidebar']}; }}
.stApp, .stMarkdown, p, span, label, li, .stRadio, .stSlider, h1, h2, h3, h4 {{ color: {p['ink']}; }}
[data-testid="stSidebar"] * {{ color: {p['ink']}; }}
.stTextInput input, [data-baseweb="input"] input, [data-baseweb="textarea"] textarea,
[data-baseweb="select"] > div {{ background: {p['panel']} !important; color: {p['ink']} !important;
    border-color: {p['border']} !important; }}
[data-testid="stExpander"] {{ border-color: {p['border']}; }}
/* example chips (secondary buttons) adapt to theme; primary stays orange */
.stButton button[kind="secondary"] {{ background:{p['panel']}; color:{p['ink']};
    border:1px solid {p['border']}; }}
.stButton button[kind="secondary"]:hover {{ border-color:{p['orange']};
    color:{p['orange']}; }}

/* brand wordmark */
.brand {{ font-size: 2.9rem; line-height: 1.05; color: {p['ink']}; margin: 0; }}
.brand .chief {{ color: {p['orange']}; }}
.brand .dot {{ color: {p['green']}; }}
.tagline {{ font-family: 'Libre Caslon Text', Georgia, serif; font-style: italic;
           color: {p['muted']}; font-size: 1.02rem; margin: 2px 0; }}
.warn {{ font-family:'Oxygen',sans-serif; font-size:.8rem; color:{p['orange_d']};
         background:{p['warn_bg']}; border:1px solid {p['warn_bd']}; border-radius:8px;
         padding:3px 10px; display:inline-block; margin-top:6px; }}

/* result cards (flex: thumbnail + body) */
.card{{display:flex; gap:14px; align-items:flex-start;
      border:1px solid {p['border']}; border-left:4px solid {p['orange']};
      border-radius:10px; padding:14px 16px; margin-bottom:12px;
      background:{p['card']}; box-shadow:0 1px 3px rgba(0,0,0,.06);}}
.thumb-wrap{{position:relative; width:104px; height:104px; flex:0 0 104px;
            border-radius:8px; overflow:hidden; background:{p['thumb']};}}
.thumb, .thumb-ph{{position:absolute; inset:0; width:100%; height:100%;}}
.thumb{{object-fit:cover;}}
.thumb-ph{{display:flex; align-items:center; justify-content:center;
          font-size:2.1rem; color:{p['muted']};}}
.card .body{{flex:1; min-width:0;}}
.card h4{{margin:0 0 6px 0; font-family:'Oxygen',sans-serif; font-weight:700;
         font-size:1.03rem; color:{p['ink']};}}
.badge{{display:inline-block; font-size:.72rem; padding:2px 9px; border-radius:999px;
       background:{p['chip']}; color:{p['muted']}; margin:0 6px 4px 0;}}
.badge-reg{{background:{p['reg_bg']}; color:{p['reg_fg']};}}
.badge-geo{{background:{p['geo_bg']}; color:{p['orange_d']};}}
.snippet{{color:{p['muted']}; font-size:.9rem; line-height:1.5; margin:6px 0;}}
.src a{{font-size:.82rem; color:{p['orange_d']}; text-decoration:none; font-weight:700;}}
.answer{{border-left:4px solid {p['green']}; background:{p['ans_bg']}; border-radius:8px;
        padding:10px 16px; color:{p['ink']};}}
</style>
"""


with st.sidebar:
    mode = st.radio("Appearance", ["Light", "Dark"], horizontal=True, key="theme",
                    format_func=lambda m: "☀️ Light" if m == "Light" else "🌙 Dark")
PAL = THEME[mode]
st.markdown(build_css(PAL), unsafe_allow_html=True)


# ── header ───────────────────────────────────────────────────────────────────
st.markdown(
    f"<div class='brand'>🍜 <span class='chief'>Chief</span>"
    f"<span style='color:{PAL['ink']}'>Epicure</span><span class='dot'>.</span></div>"
    "<div class='tagline'>Real food, real reviews — where to eat in "
    "Malaysia &amp; Singapore, always with sources.</div>"
    "<div class='warn'>⚠️ Warning: guaranteed to make you hungry.</div>",
    unsafe_allow_html=True,
)
st.write("")

coll = _collection()
stats = db_stats(coll)

with st.sidebar:
    st.divider()
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
    url = m.get("url", "")
    img = m.get("image") or ""
    # Placeholder tile sits behind the image; if the image fails to hotlink
    # (some sites block cross-origin), onerror hides it and the tile shows.
    img_tag = (f"<img class='thumb' src='{img}' loading='lazy' "
               f"onerror=\"this.style.display='none'\"/>") if img else ""
    thumb = f"<div class='thumb-wrap'><div class='thumb-ph'>🍽️</div>{img_tag}</div>"
    dist = (f"<span class='badge badge-geo'>📍 {h['distance_km']:.1f} km</span>"
            if "distance_km" in h else "")
    badges = (f"{dist}"
              f"<span class='badge badge-reg'>{REGION_LABEL.get(m.get('region',''), m.get('region',''))}</span>"
              f"<span class='badge'>{m.get('city','')}</span>"
              f"<span class='badge'>{m.get('source','')}</span>")
    snippet = (h["doc"][:300] + "…") if len(h["doc"]) > 300 else h["doc"]
    st.markdown(
        f"<div class='card'>{thumb}"
        f"<div class='body'><h4>{i}. <a href='{url}' target='_blank' "
        f"style='color:inherit;text-decoration:none'>{title}</a></h4>{badges}"
        f"<div class='snippet'>{snippet}</div>"
        f"<div class='src'>🔗 <a href='{url}' target='_blank'>{url}</a></div></div></div>",
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
        url = st.text_input("URL", placeholder="https://example.com/feed/  or  a single article URL")
        c1, c2, c3 = st.columns(3)
        kind = c1.selectbox("Type", ["auto", "page", "rss", "sitemap"],
                            help="auto = sniff from the URL. page = one article. "
                                 "rss = a feed. sitemap = an XML sitemap.")
        reg2 = c2.selectbox("Region", ["MY", "SG", ""], index=0)
        city2 = c3.text_input("City", placeholder="Kuala Lumpur")
        c4, c5 = st.columns(2)
        label = c4.text_input("Source label", value="")
        url_filter = c5.text_input("Sitemap URL filter (optional)",
                                   placeholder="/restaurants/")
        limit = st.slider("Max articles (feeds/sitemaps)", 1, 30, 10)
        save = st.checkbox("📌 Keep updated daily — add this feed to the scheduled "
                           "refresh", value=True,
                           help="Saves it to config/user_sources.yaml, which the "
                                "daily launchd/cron job re-ingests (priority 2).")
        submitted = st.form_submit_button("Add & ingest", type="primary")

    if submitted and url.strip():
        u = url.strip()
        src_label = label.strip() or (u.split("/")[2] if "://" in u else "User URL")
        with st.spinner(f"Fetching politely (≥{ingest.REQUEST_DELAY}s/host)…"):
            out = ingest.ingest_user_source(
                u, kind=kind, region=reg2, city=city2.strip(),
                source=src_label, url_filter=url_filter.strip(),
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

        if save:
            resolved = out["kind"]
            if resolved == "page":
                st.info("Saved as a single **page** — the daily job will re-check "
                        "just this one URL. Add the site's **feed** or **sitemap** "
                        "to keep pulling *new* posts.", icon="📌")
            entry = ingest.add_user_source(
                src_label, u, type=resolved, region=reg2, city=city2.strip(),
                priority=2, url_filter=url_filter.strip())
            st.success(f"📌 Saved to the daily refresh as **{entry['name']}** "
                       f"(`{entry['type']}`, priority {entry['priority']}).")

        with st.expander("Per-URL detail"):
            for r in out["results"]:
                icon = {"ok": "✅", "blocked": "🤖", "fetch_failed": "⚠️",
                        "too_short": "📄", "no_url": "❔"}.get(r["status"], "•")
                st.write(f"{icon} `{r['status']}` — {r.get('title') or r['url']} "
                         f"({r['chunks']} chunks)")
        st.caption("Switch to **Find food** — your new content is searchable now.")

    # currently-saved user feeds (part of the daily refresh)
    user_srcs = ingest.load_user_sources()
    st.divider()
    st.markdown(f"**📌 Your saved feeds — refreshed daily ({len(user_srcs)})**")
    if not user_srcs:
        st.caption("None yet. Add a feed or sitemap above and keep "
                   "“Keep updated daily” ticked.")
    else:
        for s in user_srcs:
            st.markdown(
                f"- **{s.get('name','')}** · `{s.get('type','')}` · "
                f"{REGION_LABEL.get(s.get('region',''), s.get('region','') or '—')} · "
                f"priority {s.get('priority','')}  \n"
                f"  <span style='color:{PAL['muted']};font-size:.82rem'>{s.get('url','')}</span>",
                unsafe_allow_html=True)
        st.caption("Managed in `config/user_sources.yaml`. The scheduled job runs "
                   "`ingest.py --min-priority 2`, so priority-1&2 feeds refresh daily.")
