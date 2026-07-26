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
import datetime as dt
import os
import random
from collections import Counter

import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import curate_authority as authority
import digest
import ingest
import personal
import query
import util
from util import ago, parse_pub

st.set_page_config(page_title="ChiefEpicure", page_icon="◼", layout="wide")

REGION_LABEL = {"MY": "🇲🇾 Malaysia", "SG": "🇸🇬 Singapore", "TH": "🇹🇭 Thailand",
                "ID": "🇮🇩 Indonesia", "PH": "🇵🇭 Philippines", "VN": "🇻🇳 Vietnam",
                "KH": "🇰🇭 Cambodia", "LA": "🇱🇦 Laos", "MM": "🇲🇲 Myanmar",
                "BN": "🇧🇳 Brunei", "ASEAN": "🌏 ASEAN", "MY_SG": "🌏 MY & SG", "": "—"}
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


@st.cache_data(show_spinner=False)
def load_articles(count: int):
    """One record per article (deduped by URL), newest first. `count` is the
    corpus size — passing it busts the cache whenever the store changes."""
    return util.load_articles(_collection())


# ── theme — Palantir-style monochrome (black / white / grey, Arial) ──────────
# Deliberately colourless: accents resolve to ink so the whole UI reads as
# clean black-and-white with hairline rules. Same keys as before so the CSS is
# untouched structurally; only values changed. ('orange'/'green' = ink now.)
_INK_L, _INK_D = "#111111", "#f2f2f2"
THEME = {
    "Light": dict(bg="#ffffff", sidebar="#fafafa", card="#ffffff", panel="#ffffff",
                  ink=_INK_L, muted="#6b6b6b", border="#e2e2e2", thumb="#f0f0f0",
                  orange=_INK_L, orange_d="#111111", green=_INK_L,
                  warn_bg="#f6f6f6", warn_bd="#e2e2e2",
                  reg_bg="#f0f0f0", reg_fg="#3a3a3a", geo_bg="#f0f0f0",
                  ans_bg="#fafafa", chip="#f0f0f0"),
    "Dark": dict(bg="#0b0b0c", sidebar="#121214", card="#121214", panel="#17171a",
                 ink=_INK_D, muted="#9a9a9a", border="#2a2a2c", thumb="#1e1e20",
                 orange=_INK_D, orange_d="#f2f2f2", green=_INK_D,
                 warn_bg="#161618", warn_bd="#2a2a2c",
                 reg_bg="#1e1e20", reg_fg="#cfcfcf", geo_bg="#1e1e20",
                 ans_bg="#141416", chip="#1e1e20"),
}


def build_css(p: dict) -> str:
    return f"""
<style>
/* Palantir-style: Arial, monochrome, hairline rules, sharp corners */
html, body, [class*="css"], .stMarkdown, p, div, span, label, input, textarea,
button, h1, h2, h3, h4, .brand, .sechead {{
    font-family: Arial, 'Helvetica Neue', Helvetica, sans-serif !important;
}}

/* surfaces (drive the light/dark flip) */
.stApp, [data-testid="stHeader"] {{ background: {p['bg']}; }}
[data-testid="stSidebar"] {{ background: {p['sidebar']};
    border-right: 1px solid {p['border']}; }}
.stApp, .stMarkdown, p, span, label, li, .stRadio, .stSlider, h1, h2, h3, h4 {{ color: {p['ink']}; }}
[data-testid="stSidebar"] * {{ color: {p['ink']}; }}
.stTextInput input, [data-baseweb="input"] input, [data-baseweb="textarea"] textarea,
[data-baseweb="select"] > div {{ background: {p['panel']} !important; color: {p['ink']} !important;
    border-color: {p['border']} !important; border-radius: 2px !important; }}
[data-testid="stExpander"] {{ border-color: {p['border']}; border-radius: 2px; }}

/* buttons: primary = solid ink, secondary = hairline outline; all square */
.stButton button, .stDownloadButton button {{ border-radius: 2px !important; }}
.stButton button[kind="primary"] {{ background:{p['ink']} !important; color:{p['bg']} !important;
    border:1px solid {p['ink']} !important; font-weight:700; }}
.stButton button[kind="secondary"] {{ background:{p['panel']}; color:{p['ink']};
    border:1px solid {p['border']}; }}
.stButton button[kind="secondary"]:hover {{ border-color:{p['ink']}; color:{p['ink']}; }}

/* brand wordmark — plain, tight, monochrome */
.brand {{ font-size: 2.3rem; font-weight: 800; letter-spacing: -0.03em;
         line-height: 1.05; color: {p['ink']}; margin: 0; }}
.brand .chief {{ color: {p['ink']}; }}
.brand .dot {{ color: {p['ink']}; }}
.tagline {{ text-transform: uppercase; letter-spacing: 0.18em; color: {p['muted']};
           font-size: 0.68rem; margin: 6px 0 2px; }}
.warn {{ font-size:.66rem; text-transform:uppercase; letter-spacing:.14em;
         color:{p['muted']}; border-top:1px solid {p['border']};
         border-bottom:1px solid {p['border']}; padding:5px 0; display:block;
         margin-top:8px; }}

/* result cards */
.card{{display:flex; gap:14px; align-items:flex-start;
      border:1px solid {p['border']}; border-left:2px solid {p['ink']};
      border-radius:2px; padding:14px 16px; margin-bottom:10px;
      background:{p['card']};}}
.thumb-wrap{{position:relative; width:104px; height:104px; flex:0 0 104px;
            border-radius:2px; overflow:hidden; background:{p['thumb']};
            border:1px solid {p['border']};}}
.thumb, .thumb-ph{{position:absolute; inset:0; width:100%; height:100%;}}
.thumb{{object-fit:cover;}}
.thumb-ph{{display:flex; align-items:center; justify-content:center;
          font-size:1.6rem; color:{p['muted']}; filter:grayscale(1);}}
.card .body{{flex:1; min-width:0;}}
.card h4{{margin:0 0 6px 0; font-weight:700; font-size:1.0rem; color:{p['ink']};}}
.badge{{display:inline-block; font-size:.66rem; text-transform:uppercase;
       letter-spacing:.05em; padding:2px 7px; border-radius:2px;
       background:{p['chip']}; color:{p['muted']}; margin:0 6px 4px 0;
       border:1px solid {p['border']};}}
.badge-reg{{background:{p['reg_bg']}; color:{p['reg_fg']};}}
.badge-geo{{background:{p['geo_bg']}; color:{p['ink']};}}
.snippet{{color:{p['muted']}; font-size:.86rem; line-height:1.5; margin:6px 0;}}
.src a{{font-size:.78rem; color:{p['ink']}; text-decoration:none; font-weight:700;
       border-bottom:1px solid {p['border']};}}
.answer{{border-left:2px solid {p['ink']}; background:{p['ans_bg']}; border-radius:2px;
        padding:10px 16px; color:{p['ink']};}}
/* interactive cards use bordered containers */
[data-testid="stVerticalBlockBorderWrapper"]{{border-color:{p['border']} !important;
    border-radius:2px; background:{p['card']};}}
.sechead{{font-weight:800; letter-spacing:-0.01em; font-size:1.15rem; color:{p['ink']};
         text-transform:uppercase; border-bottom:1px solid {p['border']};
         padding-bottom:4px; margin:1rem 0 .5rem;}}
.kpi{{color:{p['muted']}; font-size:.8rem; text-transform:uppercase;
     letter-spacing:.08em; margin-bottom:.5rem;}}
.stars{{color:{p['ink']}; letter-spacing:1px;}}
</style>
"""


with st.sidebar:
    mode = st.radio("Appearance", ["Light", "Dark"], horizontal=True, key="theme",
                    format_func=lambda m: "☀️ Light" if m == "Light" else "🌙 Dark")
PAL = THEME[mode]
st.markdown(build_css(PAL), unsafe_allow_html=True)


# ── header ───────────────────────────────────────────────────────────────────
st.markdown(
    "<div class='brand'>ChiefEpicure</div>"
    "<div class='tagline'>ASEAN Food Intelligence · Grounded in Sources</div>"
    "<div class='warn'>Bloggers · Creators · Michelin &amp; Authority · Updated daily</div>",
    unsafe_allow_html=True,
)
st.write("")

coll = _collection()
stats = db_stats(coll)
PREFS = personal.get_prefs()
SAVED = personal.saved_urls()
NOW = dt.datetime.now(dt.timezone.utc)

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
    st.subheader("🏙️ Your city")
    # region options are whatever's actually in the corpus (grows with ASEAN)
    _present = [r for r, _ in stats["regions"].most_common() if r]
    _reg_opts = ["All"] + sorted(_present) if _present else ["All", "MY", "SG"]
    region = st.selectbox("Region", _reg_opts,
                          index=_reg_opts.index(PREFS["region"])
                          if PREFS.get("region") in _reg_opts else 0,
                          format_func=lambda r: REGION_LABEL.get(r, r) if r != "All" else "🌏 All")
    city = st.text_input("City", value=PREFS.get("city", ""),
                         placeholder="e.g. Kuala Lumpur / Singapore")
    if st.button("📌 Save as my home city", use_container_width=True):
        personal.set_prefs(region=region, city=city.strip())
        st.toast(f"Home set to {city.strip() or region}", icon="🏙️")
        st.rerun()

    st.divider()
    st.subheader("Search options")
    k = st.slider("Results (k)", 3, 12, 6)
    with st.expander("📍 Near a point (geo)"):
        st.caption("Rank by distance to a lat,lng. Run `enrich_geo.py` first so "
                   "places carry coordinates.")
        near_str = st.text_input("lat, lng", value=PREFS.get("latlng", ""),
                                 placeholder="3.1390, 101.6869")
        radius_km = st.slider("Radius (km)", 1, 50, 10)
        if st.button("📌 Save as my location", use_container_width=True):
            personal.set_prefs(latlng=near_str.strip())
            st.toast("Home location saved", icon="📍")

    st.divider()
    if query.has_api_key():
        st.success("Claude answers: **on** (API key found)", icon="✅")
    else:
        st.info("No API key — showing ranked snippets.\nSet `ANTHROPIC_API_KEY` "
                "in `.env` for written answers.", icon="💡")

home_tab, find_tab, mylist_tab, add_tab = st.tabs(
    ["🏠  Today", "🔎  Find food", "❤️  My list", "➕  Add a source"])


# ── shared card renderer (thumbnail + body + Save) ───────────────────────────
def _thumb(img):
    tag = (f"<img class='thumb' src='{img}' loading='lazy' "
           f"onerror=\"this.style.display='none'\"/>") if img else ""
    return f"<div class='thumb-wrap'><div class='thumb-ph'>🍽️</div>{tag}</div>"


def card_from_hit(h):
    m = h["meta"]
    return {"url": m.get("url", ""), "title": m.get("title", "") or m.get("url", ""),
            "source": m.get("source", ""), "region": m.get("region", ""),
            "city": m.get("city", ""), "image": m.get("image", ""),
            "text": h["doc"], "dist": h.get("distance_km"),
            "ts": parse_pub(m.get("date", ""))}


def render_card(a, key):
    """One interactive result row: thumbnail · body · Save toggle."""
    url = a.get("url", "")
    saved = url in SAVED
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 5, 1.3], vertical_alignment="center")
        c1.markdown(_thumb(a.get("image", "")), unsafe_allow_html=True)
        bits = []
        if a.get("dist") is not None:
            bits.append(f"<span class='badge badge-geo'>📍 {a['dist']:.1f} km</span>")
        if a.get("ts"):
            bits.append(f"<span class='badge'>🕘 {ago(a['ts'])}</span>")
        reg = a.get("region", "")
        bits.append(f"<span class='badge badge-reg'>{REGION_LABEL.get(reg, reg) or '—'}</span>")
        if a.get("city"):
            bits.append(f"<span class='badge'>{a['city']}</span>")
        bits.append(f"<span class='badge'>{a.get('source','')}</span>")
        text = (a.get("text", "") or "")[:260]
        c2.markdown(
            f"<div class='body'><h4><a href='{url}' target='_blank' "
            f"style='color:inherit;text-decoration:none'>{a.get('title','')}</a></h4>"
            f"{''.join(bits)}<div class='snippet'>{text}…</div>"
            f"<div class='src'>🔗 <a href='{url}' target='_blank'>{url}</a></div></div>",
            unsafe_allow_html=True)
        if c3.button("✅ Saved" if saved else "🔖 Save", key=f"sv_{key}",
                     use_container_width=True):
            if saved:
                personal.remove_place(url)
            else:
                personal.upsert_place(
                    url, title=a.get("title", ""), source=a.get("source", ""),
                    region=a.get("region", ""), city=a.get("city", ""),
                    image=a.get("image", ""), status="want", ts=NOW.isoformat())
            st.rerun()


# ── tab: Today (aggregate what's new & good) ─────────────────────────────────
with home_tab:
    reg = None if region == "All" else region
    cty = city.strip() or None
    arts = load_articles(stats["total"])
    if reg:
        arts = [a for a in arts if a["region"] == reg]
    if cty:
        arts = [a for a in arts if cty.lower() in (a["city"] or "").lower()]

    where = city.strip() or (REGION_LABEL.get(reg, "Malaysia & Singapore") if reg
                             else "Malaysia & Singapore")
    fresh_wk = sum(1 for a in arts if a["ts"] and (NOW - a["ts"]).days < 7)
    st.markdown(f"<div class='sechead'>What's new &amp; good in "
                f"{where}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='kpi'>🆕 {fresh_wk} fresh this week · "
                f"📚 {len(arts)} places · updated daily</div>", unsafe_allow_html=True)

    # quick actions: Surprise me + today's digest
    ac1, ac2, _ = st.columns([1.1, 1.3, 3])
    if ac1.button("🎲 Surprise me", use_container_width=True) and arts:
        # bias toward "good": authority or priority-1/2, prefer with a photo
        pool = [a for a in arts if a["source"] == "Authority" or a["priority"] <= 2] or arts
        withimg = [a for a in pool if a.get("image")]
        st.session_state.surprise = random.choice(withimg or pool)["url"]
    show_digest = ac2.toggle("📬 Today's digest", value=False)

    if st.session_state.get("surprise"):
        pick = next((a for a in arts if a["url"] == st.session_state.surprise), None)
        if pick:
            st.markdown("<div class='sechead'>🎲 Tonight, try…</div>", unsafe_allow_html=True)
            render_card(pick, "surprise")

    if show_digest:
        with st.spinner("Building today's digest…"):
            md, _html, _a, _f = digest.build(region=reg, city=cty, days=7, limit=10)
        with st.container(border=True):
            st.markdown(md)
        st.download_button("⬇️ Download digest (.md)", md,
                           file_name=f"chiefepicure-{NOW:%Y-%m-%d}.md",
                           mime="text/markdown")
        st.caption("Written daily to `digests/` by the scheduled job; add SMTP env "
                   "vars to also get it emailed (see README).")

    if not arts:
        st.info("Nothing here yet for this city. Widen the filter, or add a feed "
                "under **Add a source**.")
    else:
        auth = [a for a in arts if a["source"] == "Authority"][:6]
        if auth:
            st.markdown("<div class='sechead'>⭐ Michelin &amp; authority picks</div>",
                        unsafe_allow_html=True)
            for j, a in enumerate(auth):
                render_card(a, f"auth_{j}")

        st.markdown("<div class='sechead'>🍜 Fresh finds</div>", unsafe_allow_html=True)
        dated = [a for a in arts if a["ts"] and a["source"] != "Authority"]
        undated = [a for a in arts if not a["ts"] and a["source"] != "Authority"]
        buckets = [("🆕 This week", 0, 7), ("This month", 7, 30), ("Earlier", 30, 10 ** 9)]
        shown, LIMIT = 0, 24
        for label, lo, hi in buckets:
            grp = [a for a in dated if lo <= (NOW - a["ts"]).days < hi][:LIMIT - shown]
            if not grp:
                continue
            st.markdown(f"**{label}**")
            for j, a in enumerate(grp):
                render_card(a, f"fresh_{shown + j}")
            shown += len(grp)
            if shown >= LIMIT:
                break
        if shown == 0 and undated:          # no parseable dates → just show some
            for j, a in enumerate(undated[:LIMIT]):
                render_card(a, f"undated_{j}")


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
            for i, h in enumerate(hits):
                render_card(card_from_hit(h), f"find_{i}")

            seen, lines = set(), []
            for h in hits:
                u = h["meta"].get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    lines.append(f"- [{h['meta'].get('source','')}]({u})")
            with st.expander("Sources (de-duplicated)"):
                st.markdown("\n".join(lines))


# ── tab: My list (memory of your reviews) ────────────────────────────────────
def _apply_collections(url, key):
    personal.set_collections_for(url, st.session_state.get(key, []))


def render_saved(p, key):
    """A saved place with editable status / rating / note."""
    url = p.get("url", "")
    with st.container(border=True):
        c1, c2 = st.columns([1, 6], vertical_alignment="center")
        c1.markdown(_thumb(p.get("image", "")), unsafe_allow_html=True)
        stars = "★" * int(p.get("rating", 0)) + "☆" * (5 - int(p.get("rating", 0)))
        c2.markdown(
            f"<div class='body'><h4><a href='{url}' target='_blank' "
            f"style='color:inherit;text-decoration:none'>{p.get('title','')}</a></h4>"
            f"<span class='badge badge-reg'>{REGION_LABEL.get(p.get('region',''), p.get('region','')) or '—'}</span>"
            f"<span class='badge'>{p.get('city','')}</span>"
            f"<span class='badge'>{p.get('source','')}</span>"
            f"<span class='stars'>&nbsp;{stars}</span></div>",
            unsafe_allow_html=True)
        e1, e2, e3 = st.columns([1.4, 1.6, 0.7], vertical_alignment="bottom")
        status = e1.radio("Status", ["want", "been"], horizontal=True,
                          index=0 if p.get("status", "want") == "want" else 1,
                          format_func=lambda s: "🍽️ Want to go" if s == "want" else "✅ Been",
                          key=f"stt_{key}")
        rating = e2.slider("My rating", 0, 5, int(p.get("rating", 0)), key=f"rt_{key}")
        remove = e3.button("🗑️", key=f"rm_{key}", help="Remove from list")
        note = st.text_input("My note", value=p.get("note", ""),
                             placeholder="what I had, what to order next time…",
                             key=f"nt_{key}")
        cols_all = list(personal.load_collections().keys())
        if cols_all:
            st.multiselect("Collections", options=cols_all,
                           default=personal.collections_for(url),
                           key=f"col_{key}", on_change=_apply_collections,
                           args=(url, f"col_{key}"))
        changed = (status != p.get("status", "want") or rating != int(p.get("rating", 0))
                   or note != p.get("note", ""))
        if remove:
            personal.remove_place(url)
            st.rerun()
        if changed and st.button("💾 Save review", key=f"sr_{key}", type="primary"):
            personal.upsert_place(url, status=status, rating=rating, note=note.strip())
            st.toast("Saved your review", icon="💾")
            st.rerun()


with mylist_tab:
    places = personal.load_places()
    if not places:
        st.info("Your list is empty. Tap **🔖 Save** on any card in **Today** or "
                "**Find food**, then come back to rate it and jot a note.", icon="❤️")
    else:
        want = [p for p in places if p.get("status", "want") == "want"]
        been = [p for p in places if p.get("status") == "been"]
        st.markdown(f"<div class='kpi'>❤️ {len(places)} saved · ✅ {len(been)} been · "
                    f"🍽️ {len(want)} want to go</div>", unsafe_allow_html=True)

        # ── collections (named lists) ────────────────────────────────────────
        by_url = {p["url"]: p for p in places}
        cols = personal.load_collections()
        with st.expander(f"📚 Collections ({len(cols)})", expanded=bool(cols)):
            nc1, nc2 = st.columns([3, 1])
            new_name = nc1.text_input("New collection", key="new_col",
                                      placeholder="e.g. Date night, Cheap eats, Omakase",
                                      label_visibility="collapsed")
            if nc2.button("➕ Create", use_container_width=True) and new_name.strip():
                personal.create_collection(new_name.strip())
                st.rerun()
            for name, urls in cols.items():
                titles = [f"[{by_url[u]['title']}]({u})" for u in urls if u in by_url]
                cc1, cc2 = st.columns([5, 1])
                cc1.markdown(f"**{name}** ({len(titles)}) — "
                             + (" · ".join(titles) if titles else "_empty_"))
                if cc2.button("🗑️", key=f"delc_{name}", help=f"Delete '{name}'"):
                    personal.delete_collection(name)
                    st.rerun()
            if cols:
                st.caption("Assign a place to collections from its card below.")

        if want:
            st.markdown("<div class='sechead'>🍽️ Want to go</div>", unsafe_allow_html=True)
            for i, p in enumerate(want):
                render_saved(p, f"want_{i}")
        if been:
            st.markdown("<div class='sechead'>✅ Been there</div>", unsafe_allow_html=True)
            for i, p in enumerate(been):
                render_saved(p, f"been_{i}")

        # ── recommended for you (from your memory) ───────────────────────────
        st.markdown("<div class='sechead'>✨ Recommended for you</div>",
                    unsafe_allow_html=True)
        seed_titles = [p.get("title", "") for p in places if p.get("title")][:6]
        if not seed_titles:
            st.caption("Save a few places and I'll suggest similar ones.")
        else:
            st.caption("Because you saved: " + ", ".join(t[:30] for t in seed_titles[:3])
                       + ("…" if len(seed_titles) > 3 else ""))
            with st.spinner("Finding places that match your taste…"):
                recs = query.retrieve(" ; ".join(seed_titles), k=12,
                                      embedder=_embedder(), coll=coll)
            saved_now = personal.saved_urls()
            fresh = [h for h in recs if h["meta"].get("url") not in saved_now][:6]
            if not fresh:
                st.caption("No new suggestions yet — add more sources to widen the pool.")
            for i, h in enumerate(fresh):
                render_card(card_from_hit(h), f"rec_{i}")


# ── tab: add a source ────────────────────────────────────────────────────────
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

    # ── add a Michelin / authority pick (e.g. from the printed guide) ────────
    st.divider()
    st.markdown("### ⭐ Add a Michelin / authority pick")
    st.caption("For entries from a printed guide (e.g. the MICHELIN Guide KL/Penang) "
               "or Asia's 50 Best. These are **curated facts, never scraped** — "
               "enter name / stars / cuisine only, not the guide's review text. "
               "They show under **Today → ⭐ Michelin & authority picks**.")
    STAR_OPTS = ["", "1 MICHELIN Star", "2 MICHELIN Stars", "3 MICHELIN Stars",
                 "Bib Gourmand", "MICHELIN Selected", "Asia's 50 Best"]
    with st.form("add_authority", clear_on_submit=True):
        an = st.text_input("Restaurant name", placeholder="e.g. Dewakan")
        ac1, ac2, ac3 = st.columns(3)
        acity = ac1.text_input("City", placeholder="Kuala Lumpur / George Town")
        areg = ac2.selectbox("Region", ["MY", "SG"], index=0)
        astars = ac3.selectbox("Distinction", STAR_OPTS, index=0)
        ac4, ac5 = st.columns(2)
        acuisine = ac4.text_input("Cuisine", placeholder="Modern Malaysian")
        aurl = ac5.text_input("Website / guide URL (optional)",
                              placeholder="https://…")
        anote = st.text_input("Your own one-line note (optional)",
                              placeholder="e.g. tasting menu; book ahead")
        asub = st.form_submit_button("⭐ Add pick", type="primary")
    if asub:
        if not an.strip():
            st.warning("A restaurant name is required.")
        else:
            row = {"name": an.strip(), "city": acity.strip(), "region": areg,
                   "stars": astars, "cuisine": acuisine.strip(),
                   "url": aurl.strip(), "note": anote.strip()}
            authority.append_csv(row)
            authority.add_rows([row], embedder=_embedder(), coll=coll)
            st.success(f"⭐ Added **{row['name']}**"
                       + (f" ({astars})" if astars else "")
                       + " to your authority picks and saved it to "
                       "`config/curated_authority.csv`.")

    try:
        n_auth = len(coll.get(where={"source": "Authority"})["ids"])
    except Exception:
        n_auth = 0
    st.caption(f"You currently have **{n_auth}** authority picks. Bulk-edit them any "
               "time in `config/curated_authority.csv`, then rerun "
               "`python curate_authority.py`.")
