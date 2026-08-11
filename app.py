#!/usr/bin/env python3
"""
Autonom — Streamlit UI for the FoodRAG "where to eat"system.

    streamlit run app.py

A friendly front-end over the same local pipeline used by the CLI:
  • Find food — semantic search over ingested MY/SG reviews, with a cited
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

import auth
import community
import curate_authority as authority
import digest
import facets
import geo_gazetteer
import ingest
import ai_tags
import personal
import query
import recommender
import tags
import util
from util import ago, parse_pub

st.set_page_config(page_title="Autonom", page_icon="", layout="wide")

# Cloud (Render): start the once-a-day Instagram refresh inside the web process
# — the only way to update the disk-backed store (Render crons can't mount the
# disk). Guarded by env + cache_resource so it runs once and never locally.
if os.getenv("AUTONOM_CLOUD_REFRESH"):
    @st.cache_resource
    def _cloud_bg():
        import cloud_refresh
        import cloud_sync
        cloud_sync.start()      # refresh curated core if the bundled workbook changed
        cloud_refresh.start()   # daily Instagram pull
        return True
    _cloud_bg()

REGION_LABEL = {"MY": "Malaysia", "SG": "Singapore", "TH": "Thailand",
                "ID": "Indonesia", "PH": "Philippines", "VN": "Vietnam",
                "KH": "Cambodia", "LA": "Laos", "MM": "Myanmar",
                "BN": "Brunei", "ASEAN": "ASEAN", "MY_SG": "MY & SG", "": "—"}
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


@st.cache_data(show_spinner=False)
def facet_options(count: int):
    """Food-type options actually present in the corpus (most common first).
    `count` busts the cache when the store changes."""
    got = _collection().get(include=["metadatas"])
    ft = Counter((m.get("food_type") or "").strip()
                 for m in got.get("metadatas", []) or [])
    return [t for t, _ in ft.most_common() if t]


@st.cache_data(show_spinner=False)
def browse_cards(count, region, area, cuisine, acc, price, ft, limit=24):
    """Rich, full-metadata cards from the whole corpus for the magazine grid —
    filtered by the top bar, deduped by (title, city), best places first."""
    got = _collection().get(include=["metadatas", "documents"])
    metas, docs = got.get("metadatas", []) or [], got.get("documents", []) or []
    terms = [t.strip().lower() for t in (area, cuisine) if t and t.strip()]
    seen, rows = set(), []
    for m, d in zip(metas, docs):
        if region and region != "All" and m.get("region") != region:
            continue
        if not facets.passes(m, acc, price, ft):
            continue
        title = (m.get("title") or "").strip()
        if not title:
            continue
        key = (title.lower(), (m.get("city") or "").lower())
        if key in seen:
            continue
        if terms:
            hay = (f"{title} {m.get('city','')} {m.get('address','')} "
                   f"{m.get('cuisine','')} {d}").lower()
            if not all(t in hay for t in terms):
                continue
        seen.add(key)
        rows.append({"url": m.get("url", ""), "title": title,
                     "source": m.get("source", ""), "region": m.get("region", ""),
                     "city": m.get("city", ""), "image": m.get("image", ""),
                     "text": d, "address": m.get("address", ""),
                     "phone": m.get("phone", ""), "hours": m.get("hours", ""),
                     "maps": m.get("maps", ""), "price": m.get("price", ""),
                     "accolades": m.get("accolades", ""), "rating": m.get("rating", 0),
                     "order": m.get("order", ""), "cuisine": m.get("cuisine", ""),
                     "food_type": m.get("food_type", ""), "dist": None, "ts": None})

    def _score(a):
        w = {"MICHELIN Star": 3, "Bib Gourmand": 2, "MICHELIN Selected": 1}.get(
            facets.accolade_tier(a), 0)
        try:
            rt = float(a.get("rating") or 0)
        except (TypeError, ValueError):
            rt = 0
        return (w, rt, 1 if a.get("image") else 0)
    rows.sort(key=_score, reverse=True)
    return rows[:limit], len(seen)


@st.cache_data(show_spinner="Placing pins…")
def map_points(count: int, region, area, cuisine, acc, price, ft):
    """Whole-corpus mappable rows, deduped by (title, city) and filtered by
    the sidebar region/area/cuisine + facets. Each row is snapped to an area
    centroid via the offline gazetteer. Returns (rows, n_unmapped)."""
    got = _collection().get(include=["metadatas", "documents"])
    metas, docs = got.get("metadatas", []) or [], got.get("documents", []) or []
    terms = [t.strip().lower() for t in (area, cuisine) if t and t.strip()]
    rows, seen, unmapped = [], set(), 0
    for m, d in zip(metas, docs):
        if region and region != "All"and m.get("region") != region:
            continue
        if not facets.passes(m, acc, price, ft):
            continue
        title = (m.get("title") or "").strip()
        if not title:
            continue
        key = (title.lower(), (m.get("city") or "").lower())
        if key in seen:
            continue
        if terms:
            hay = (f"{title} {m.get('city','')} {m.get('address','')} "
                   f"{m.get('cuisine','')} {d}").lower()
            if not all(t in hay for t in terms):
                continue
        seen.add(key)
        loc = geo_gazetteer.locate(m)
        if not loc:
            unmapped += 1
            continue
        lat, lng, prec = loc
        rows.append({
            "lat": lat, "lng": lng, "precision": prec, "title": title,
            "city": m.get("city", ""), "region": m.get("region", ""),
            "cuisine": m.get("cuisine", "") or m.get("food_type", ""),
            "accolades": m.get("accolades", ""), "price": m.get("price", ""),
            "address": m.get("address", ""), "url": m.get("url", ""),
            "phone": m.get("phone", ""), "hours": m.get("hours", ""),
            "source": m.get("source", ""), "image": m.get("image", ""),
            "acc_tier": facets.accolade_tier(m),
        })
    return rows, unmapped


# ── theme — warm editorial "foodie magazine" (Eatbook / SethLui / MissTamChiak) ─
# Clean warm neutrals + one terracotta accent; photos supply the colour.
THEME = {
    "Light": dict(bg="#faf7f2", sidebar="#f4efe7", card="#ffffff", panel="#ffffff",
                  ink="#201b15", muted="#8c8377", border="#ece5da", thumb="#efe8dd",
                  accent="#d9542b", accent_soft="#fbeee8",
                  orange="#d9542b", orange_d="#b8431f", green="#2e7d32",
                  warn_bg="#fbeee8", warn_bd="#f0d9cd",
                  reg_bg="#f2ece2", reg_fg="#5a5347", geo_bg="#eef4ee",
                  ans_bg="#fbf8f3", chip="#f3ede4"),
    "Dark": dict(bg="#15120e", sidebar="#1b1610", card="#1e1913", panel="#221c15",
                 ink="#f1ece2", muted="#a89e8f", border="#2e2820", thumb="#2a2319",
                 accent="#e2673f", accent_soft="#2a1c15",
                 orange="#e2673f", orange_d="#e2673f", green="#7bbf80",
                 warn_bg="#2a1c15", warn_bd="#3a281d",
                 reg_bg="#272119", reg_fg="#cabfae", geo_bg="#1c241c",
                 ans_bg="#1e1913", chip="#272119"),
}


def build_css(p: dict) ->str:
    return f"""
<style>
html, body, [class*="css"], .stMarkdown, p, div, span, label, input, textarea, button,
h1, h2, h3, h4 {{ font-family: 'Helvetica Neue', Arial, sans-serif; }}
.brand, .hero-h, .sechead {{ font-family: Georgia, 'Times New Roman', serif; }}

/* hide the (now unused) sidebar entirely */
[data-testid="stSidebar"], [data-testid="collapsedControl"],
button[title="Open sidebar"], button[title="Close sidebar"] {{ display:none !important; }}
.block-container {{ max-width: 1180px; padding-top: 2.2rem; }}

/* surfaces */
.stApp, [data-testid="stHeader"] {{ background: {p['bg']}; }}
.stApp, .stMarkdown, p, span, label, li, .stRadio, .stSlider, h1, h2, h3, h4 {{ color: {p['ink']}; }}
.stTextInput input, [data-baseweb="input"] input, [data-baseweb="textarea"] textarea,
[data-baseweb="select"] >div, [data-testid="stChatInput"] textarea {{
    background: {p['panel']} !important; color: {p['ink']} !important;
    border-color: {p['border']} !important; border-radius: 10px !important; }}
[data-testid="stChatInput"] {{ border:1px solid {p['border']}; border-radius:14px;
    background:{p['panel']}; box-shadow:0 2px 12px rgba(0,0,0,.05); }}
[data-testid="stExpander"] {{ border-color: {p['border']}; border-radius:12px;
    background:{p['card']}; }}

/* buttons: primary = accent, secondary = hairline */
.stButton button, .stDownloadButton button {{ border-radius: 999px !important; font-weight:600; }}
.stButton button[kind="primary"] {{ background:{p['accent']} !important; color:#fff !important;
    border:1px solid {p['accent']} !important; }}
.stButton button[kind="secondary"] {{ background:{p['panel']}; color:{p['ink']};
    border:1px solid {p['border']}; }}
.stButton button[kind="secondary"]:hover {{ border-color:{p['accent']}; color:{p['accent']}; }}
.stTabs [data-baseweb="tab-highlight"] {{ background:{p['accent']}; }}
.stTabs [aria-selected="true"] {{ color:{p['accent']} !important; }}

/* masthead */
.brand {{ font-size: 2.5rem; font-weight: 700; letter-spacing:-0.01em; line-height:1;
         color: {p['ink']}; margin: 0; }}
.brand .dot {{ color: {p['accent']}; }}
.tagline {{ color: {p['muted']}; font-size: 0.98rem; margin: 6px 0 2px; }}
.warn {{ font-size:.72rem; font-weight:600; letter-spacing:.03em; text-transform:uppercase;
         color:{p['accent']}; background:{p['accent_soft']};
         border-radius:999px; padding:4px 12px; display:inline-block; margin-top:8px; }}
.sechead{{font-weight:700; letter-spacing:-0.01em; font-size:1.5rem; color:{p['ink']};
         margin:1.4rem 0 .7rem;}}
.kpi{{color:{p['muted']}; font-size:.85rem; margin-bottom:.6rem;}}

/* ── magazine grid card ── */
.pcard{{border:1px solid {p['border']}; border-radius:16px; overflow:hidden;
       background:{p['card']}; margin-bottom:4px; transition:box-shadow .15s, transform .15s;
       box-shadow:0 1px 2px rgba(0,0,0,.04);}}
.pcard:hover{{box-shadow:0 10px 26px rgba(0,0,0,.10); transform:translateY(-2px);}}
.pthumb{{position:relative; width:100%; aspect-ratio:16/10; overflow:hidden; background:{p['thumb']};}}
.pthumb img{{width:100%; height:100%; object-fit:cover; display:block;}}
.pph{{position:absolute; inset:0; display:flex; align-items:flex-end; padding:12px;
     background:linear-gradient(135deg, {p['accent_soft']}, {p['thumb']});}}
.pph b{{font-family:Georgia,serif; font-size:1.1rem; color:{p['muted']}; line-height:1.2;}}
.pribbon{{position:absolute; top:10px; left:10px; background:{p['accent']}; color:#fff;
         font-size:.66rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
         padding:3px 9px; border-radius:999px;}}
.pbody{{padding:12px 14px 14px;}}
.ptitle{{font-weight:700; font-size:1.06rem; line-height:1.25; color:{p['ink']};
        margin:0 0 6px; letter-spacing:-0.01em;}}
.ptitle a{{color:inherit; text-decoration:none;}}
.pmeta{{margin:2px 0;}}
.psnip{{color:{p['muted']}; font-size:.85rem; line-height:1.5; margin:8px 0 4px;}}

/* tag chips */
.tag{{display:inline-block; font-size:.7rem; font-weight:600; padding:3px 9px;
     border-radius:999px; background:{p['chip']}; color:{p['reg_fg']};
     margin:0 5px 5px 0; border:1px solid {p['border']}; white-space:nowrap;}}
.tag-acc{{background:{p['accent_soft']}; color:{p['accent']}; border-color:{p['accent_soft']};}}
.tag-open{{color:{p['green']}; border-color:{p['green']}; background:transparent;}}
.tag-rate{{background:{p['ink']}; color:{p['bg']}; border-color:{p['ink']};}}
.tag-ai{{opacity:.85; font-style:italic;}}

/* legacy classes kept (list card / contact / rec / hero) restyled to match */
.card{{display:flex; gap:16px; align-items:flex-start; border:1px solid {p['border']};
      border-radius:16px; padding:12px 14px; margin-bottom:12px; background:{p['card']};}}
.thumb-wrap{{position:relative; width:120px; height:120px; flex:0 0 120px;
            border-radius:12px; overflow:hidden; background:{p['thumb']};}}
.thumb, .thumb-ph{{position:absolute; inset:0; width:100%; height:100%;}}
.thumb{{object-fit:cover;}}
.thumb-ph{{display:flex; align-items:center; justify-content:center; font-size:.8rem;
          color:{p['muted']}; text-align:center; padding:6px;}}
.card .body{{flex:1; min-width:0;}} .card h4{{margin:0 0 6px; font-weight:700; font-size:1.05rem;}}
.badge{{display:inline-block; font-size:.7rem; font-weight:600; padding:3px 9px;
       border-radius:999px; background:{p['chip']}; color:{p['reg_fg']};
       margin:0 5px 5px 0; border:1px solid {p['border']};}}
.badge-reg{{background:{p['accent_soft']}; color:{p['accent']}; border-color:{p['accent_soft']};}}
.badge-geo{{background:{p['geo_bg']}; color:{p['green']};}}
.snippet{{color:{p['muted']}; font-size:.88rem; line-height:1.55; margin:6px 0;}}
.src a{{font-size:.8rem; color:{p['accent']}; text-decoration:none; font-weight:600;}}
.answer{{border:1px solid {p['border']}; background:{p['ans_bg']}; border-radius:14px;
        padding:14px 18px; color:{p['ink']};}}
[data-testid="stVerticalBlockBorderWrapper"]{{border-color:{p['border']} !important;
    border-radius:16px; background:{p['card']};}}
[data-testid="stChatMessage"]{{background:{p['card']}; border:1px solid {p['border']}; border-radius:14px;}}
[data-testid*="Avatar"], [data-testid*="chatAvatarIcon"] {{ display:none !important; }}
[data-testid="stChatMessage"] {{ gap:0 !important; }}
.stars{{color:{p['accent']}; letter-spacing:1px;}}
.hero-h{{font-weight:700; font-size:2.2rem; letter-spacing:-0.01em; color:{p['ink']};
        text-align:center; margin:1.6rem 0 .3rem;}}
.hero-s{{color:{p['muted']}; text-align:center; margin-bottom:1.1rem;}}
.contact{{font-size:.8rem; color:{p['muted']}; line-height:1.55; margin-top:6px;}}
.contact a{{color:{p['accent']}; text-decoration:none;}}
.rec{{border:1px solid {p['border']}; border-radius:14px; padding:12px 15px;
     margin:9px 0; background:{p['card']};}}
.rec-h{{font-weight:700; font-size:1.06rem; letter-spacing:-0.01em; color:{p['ink']};}}
.rec-h .rank{{color:{p['accent']}; font-weight:800; margin-right:6px;}}
.chips{{margin:5px 0 2px;}}
.rec-intro{{color:{p['ink']}; margin:2px 0 8px; line-height:1.6;}}
</style>
"""


def render_account():
    """Sidebar account box — sign in / register, or show the signed-in member."""
    user = st.session_state.get("user")
    if user:
        st.markdown(f"** {auth.display_name(user)}**")
        if st.button("Sign out", use_container_width=True):
            st.session_state.user = None
            st.rerun()
        return
    with st.expander("Sign in / Register", expanded=False):
        tab_in, tab_up = st.tabs(["Sign in", "Register"])
        with tab_in:
            u = st.text_input("Username", key="li_u")
            p = st.text_input("Password", type="password", key="li_p")
            if st.button("Sign in", key="li_go", use_container_width=True, type="primary"):
                if auth.verify(u, p):
                    st.session_state.user = auth.normalize_username(u)
                    st.rerun()
                else:
                    st.error("Wrong username or password.")
        with tab_up:
            du = st.text_input("Display name", key="su_d")
            u2 = st.text_input("Username", key="su_u")
            p2 = st.text_input("Password (8+ chars)", type="password", key="su_p")
            if st.button("Create account", key="su_go", use_container_width=True,
                         type="primary"):
                ok, msg = auth.create_user(u2, p2, display=du)
                if ok:
                    st.session_state.user = auth.normalize_username(u2)
                    st.rerun()
                else:
                    st.error(msg)
        st.caption("Local prototype accounts (hashed). Not for public deployment.")


if "user"not in st.session_state:
    st.session_state.user = None

# ── top bar (replaces the sidebar): masthead · theme · account ───────────────
_tl, _tr = st.columns([3, 1], vertical_alignment="top")
with _tr:
    mode = st.radio("Theme", ["Light", "Dark"], horizontal=True, key="theme",
                    label_visibility="collapsed")
PAL = THEME[mode]
st.markdown(build_css(PAL), unsafe_allow_html=True)
personal.use(st.session_state.user)      # scope saved places / reviews to the member
USER = st.session_state.user
DISPLAY = auth.display_name(USER) if USER else "guest"
_tl.markdown(
    "<div class='brand'>Autonom<span class='dot'>.</span></div>"
    "<div class='tagline'>Your food guide for Malaysia &amp; Singapore — real "
    "places, richly tagged and searchable.</div>"
    "<div class='warn'>Curated · Member-reviewed · No scraping</div>",
    unsafe_allow_html=True)
with _tr:
    render_account()
st.write("")

coll = _collection()
stats = db_stats(coll)
PREFS = personal.get_prefs()
SAVED = personal.saved_urls()
NOW = dt.datetime.now(dt.timezone.utc)

# ── filter bar (replaces the sidebar filters) ────────────────────────────────
_present = [r for r, _ in stats["regions"].most_common() if r]
_reg_opts = ["All"] + sorted(_present) if _present else ["All", "MY", "SG"]
_fb = st.columns([1.1, 1.5, 1.6, 1.3, 1.2])
region = _fb[0].selectbox("Region", _reg_opts,
                          index=_reg_opts.index(PREFS["region"])
                          if PREFS.get("region") in _reg_opts else 0,
                          format_func=lambda r: REGION_LABEL.get(r, r) if r != "All" else "All")
area = _fb[1].text_input("District / city", value=PREFS.get("city", ""),
                         placeholder="Bangsar · Penang · Tiong Bahru")
cuisine = _fb[2].text_input("Cuisine / dish",
                            placeholder="laksa · omakase · nasi lemak")
acc_sel = _fb[3].selectbox("Accolade", facets.ACCOLADE_OPTS, index=0)
price_sel = _fb[4].selectbox("Price", facets.PRICE_OPTS, index=0)
with st.expander("More filters · location · save home"):
    _mc = st.columns([1.6, 1.2, 1, 1])
    _ft_opts = ["All"] + facet_options(stats["total"])
    ft_sel = _mc[0].selectbox("Food type", _ft_opts, index=0)
    near_str = _mc[1].text_input("Near me (lat, lng)", value=PREFS.get("latlng", ""),
                                 placeholder="3.139, 101.687")
    radius_km = _mc[2].slider("Radius (km)", 1, 50, 10)
    k = _mc[3].slider("Results", 3, 12, 6)
    _sb1, _sb2, _ = st.columns([1, 1, 3])
    if _sb1.button("Save as my home", use_container_width=True):
        personal.set_prefs(region=region, city=area.strip())
        st.toast(f"Home set to {area.strip() or region}")
        st.rerun()
    if _sb2.button("Save my location", use_container_width=True):
        personal.set_prefs(latlng=near_str.strip())
        st.toast("Location saved")
    st.caption("Claude answers on" if query.has_api_key()
               else "No API key — ranked snippets")
st.write("")

chat_tab, home_tab, find_tab, map_tab, mylist_tab, contribute_tab, add_tab = st.tabs(
    ["Ask", "Discover", "Find", "Map", "My list",
     "Contribute", "Add a place"])


# ── shared card renderer (thumbnail + body + Save) ───────────────────────────
def _thumb(img):
    tag = (f"<img class='thumb' src='{img}' loading='lazy'"
           f"onerror=\"this.style.display='none'\"/>") if img else ""
    return f"<div class='thumb-wrap'><div class='thumb-ph'></div>{tag}</div>"


def contact_html(a) ->str:
    """Location + address + contact block, from whatever fields are present."""
    rows = []
    if a.get("address"):
        rows.append(a["address"])
    line2 = []
    if a.get("phone"):
        line2.append(f"Tel {a['phone']}")
    if a.get("hours"):
        line2.append(f"Hours {a['hours']}")
    if line2:
        rows.append(" · ".join(line2))
    links = []
    if a.get("maps"):
        links.append(f"<a href='{a['maps']}' target='_blank'>Directions</a>")
    if a.get("url", "").startswith("http") and "instagram"in a.get("url", ""):
        links.append(f"<a href='{a['url']}' target='_blank'>Instagram</a>")
    if not rows and not links:
        return ""
    body = "<br/>".join(rows)
    if links:
        body += ("<br/>"if body else "") + " · ".join(links)
    return f"<div class='contact'>{body}</div>"


def rec_html(hit, rank_no) ->str:
    """A styled Makan Kaki recommendation card: title · reason chips · contact."""
    m = hit["meta"]
    title = m.get("title", "") or "—"
    maps = m.get("maps", "")
    tlink = (f"<a href='{maps}' target='_blank' style='color:inherit;"
             f"text-decoration:none'>{title}</a>") if maps else title
    chips = "".join(f"<span class='badge'>{r}</span>"for r in hit.get("reasons", []))
    snip = (hit.get("doc", "") or "")[:170]
    return (f"<div class='rec'><div class='rec-h'><span class='rank'>{rank_no}.</span>"
            f"{tlink}</div><div class='chips'>{chips}</div>"
            f"{contact_html(m)}<div class='snippet'>{snip}…</div></div>")


def card_from_hit(h):
    m = h["meta"]
    return {"url": m.get("url", ""), "title": m.get("title", "") or m.get("url", ""),
            "source": m.get("source", ""), "region": m.get("region", ""),
            "city": m.get("city", ""), "image": m.get("image", ""),
            "text": h["doc"], "dist": h.get("distance_km"),
            "ts": parse_pub(m.get("date", "")),
            "address": m.get("address", ""), "phone": m.get("phone", ""),
            "hours": m.get("hours", ""), "maps": m.get("maps", ""),
            "price": m.get("price", ""), "accolades": m.get("accolades", ""),
            "rating": m.get("rating", 0), "order": m.get("order", ""),
            "cuisine": m.get("cuisine", ""), "food_type": m.get("food_type", "")}


_BAD_CITY = {"", "(state not stated)", "(city not stated)", "-"}


def place_tags(a) -> list:
    """Full, NEVER-empty tag set for an entry: region · district · setting ·
    price · accolade · rating · open-now · meal. Fills gaps from deterministic
    rules, then the cached GenAI tags, then a generic fallback. Returns
    (label, css_class) chips."""
    ai = ai_tags.get(a)
    chips = []
    reg = a.get("region", "")
    chips.append((REGION_LABEL.get(reg, reg) or "Asia", "tag-acc"))
    dist = tags.district(a) or (a.get("city") if a.get("city", "").lower() not in _BAD_CITY else "") \
        or ai.get("district", "")
    if dist:
        chips.append((dist, "tag"))
    setg = tags.setting(a) or a.get("cuisine") or ai.get("cuisine") or ai.get("setting") \
        or a.get("food_type") or "Eatery"
    chips.append((setg[:26], "tag"))
    for dish in (ai.get("dishes") or [])[:2]:
        if dish:
            chips.append((dish[:22], "tag tag-ai"))
    band = facets.price_band(a)
    if band:
        chips.append((band, "tag"))
    tier = facets.accolade_tier(a)
    if tier:
        chips.append((a.get("accolades") or tier, "tag-acc"))
    try:
        rt = float(a.get("rating") or 0)
    except (TypeError, ValueError):
        rt = 0
    if rt:
        chips.append((f"★ {rt:g}", "tag-rate"))
    state = recommender.open_state(a.get("hours", "") or "")
    if state == "open":
        chips.append(("Open now", "tag-open"))
    meal = tags.meal(a)
    if meal:
        chips.append((meal, "tag"))
    return chips


def chips_html(a) -> str:
    return "".join(f"<span class='{cls}'>{lbl}</span>" for lbl, cls in place_tags(a))


def is_thin(a) -> bool:
    """Low-confidence entry — worth asking a member to verify/complete."""
    return not (a.get("address") or "").strip() and not (a.get("hours") or "").strip()


def _place_label(a) -> str:
    return (a.get("cuisine") or tags.setting(a) or ai_tags.get(a).get("cuisine")
            or REGION_LABEL.get(a.get("region", ""), "Autonom") or "Autonom")


def thumb_inner(a) -> str:
    """Inner of the grid thumbnail — real photo if present, else placeholder tile."""
    img = community.served_url(a.get("image", "")) if a.get("image") else ""
    if img:
        return f"<img src='{img}' loading='lazy' onerror=\"this.style.display='none'\"/>"
    return f"<div class='pph'><b>{_place_label(a)[:28]}</b></div>"


def card_html(a) -> str:
    url = a.get("url", "")
    acc = facets.accolade_tier(a)
    ribbon = f"<div class='pribbon'>{acc}</div>" if acc in facets._MICHELIN else ""
    title = a.get("title", "") or "—"
    tlink = (f"<a href='{url}' target='_blank'>{title}</a>"
             if str(url).startswith("http") else title)
    return (f"<div class='pcard'><div class='pthumb'>{ribbon}{thumb_inner(a)}</div>"
            f"<div class='pbody'><div class='ptitle'>{tlink}</div>"
            f"<div class='pmeta'>{chips_html(a)}</div>{contact_html(a)}</div></div>")


def _toggle_save(a):
    url = a.get("url", "")
    if url in SAVED:
        personal.remove_place(url)
    else:
        personal.upsert_place(url, title=a.get("title", ""), source=a.get("source", ""),
                              region=a.get("region", ""), city=a.get("city", ""),
                              image=a.get("image", ""), status="want", ts=NOW.isoformat())


def render_grid(items, key_prefix, cols=3):
    """Image-forward magazine grid; each card has Save (+ Verify when thin)."""
    for i in range(0, len(items), cols):
        for col, a in zip(st.columns(cols), items[i:i + cols]):
            with col:
                st.markdown(card_html(a), unsafe_allow_html=True)
                kk = f"{key_prefix}_{i}_{(a.get('title') or '')[:10]}"
                saved = a.get("url", "") in SAVED
                if is_thin(a):
                    b1, b2 = st.columns(2)
                    if b1.button("Saved" if saved else "Save", key=f"gsv_{kk}",
                                 use_container_width=True):
                        _toggle_save(a)
                        st.rerun()
                    if b2.button("Verify", key=f"gvf_{kk}", use_container_width=True):
                        st.session_state["verify_place"] = a.get("title", "")
                        st.toast("Add the details under Contribute to verify this listing.")
                elif st.button("Saved" if saved else "Save", key=f"gsv_{kk}",
                               use_container_width=True):
                    _toggle_save(a)
                    st.rerun()


def facets_active() ->bool:
    return acc_sel != "Any"or price_sel != "Any"or ft_sel != "All"


def apply_facets(hits):
    """Keep only hits whose metadata satisfies the active sidebar facets."""
    if not facets_active():
        return hits
    return [h for h in hits if facets.passes(h["meta"], acc_sel, price_sel, ft_sel)]


def render_card(a, key):
    """One interactive result row: thumbnail · body · Save toggle."""
    url = a.get("url", "")
    saved = url in SAVED
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 5, 1.3], vertical_alignment="center")
        c1.markdown(_thumb(a.get("image", "")), unsafe_allow_html=True)
        dist_chip = (f"<span class='tag tag-open'>{a['dist']:.1f} km</span>"
                     if a.get("dist") is not None else "")
        text = (a.get("text", "") or "")[:240]
        src_line = (f"<div class='src'><a href='{url}' target='_blank'>{url}</a></div>"
                    if str(url).startswith("http") else "")
        c2.markdown(
            f"<div class='body'><h4><a href='{url}' target='_blank'"
            f"style='color:inherit;text-decoration:none'>{a.get('title','')}</a></h4>"
            f"{dist_chip}{chips_html(a)}<div class='snippet'>{text}…</div>"
            f"{contact_html(a)}{src_line}</div>",
            unsafe_allow_html=True)
        if c3.button("Saved"if saved else "Save", key=f"sv_{key}",
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
    reg = None if region == "All"else region
    terms = [t.strip().lower() for t in (area, cuisine) if t and t.strip()]
    arts = load_articles(stats["total"])
    if reg:
        arts = [a for a in arts if a["region"] == reg]
    if terms:
        def _hay(a):
            return (f"{a['title']} {a.get('city', '')} {a.get('source', '')} "
                    f"{a['text']}").lower()
        arts = [a for a in arts if all(t in _hay(a) for t in terms)]

    where = area.strip() or (REGION_LABEL.get(reg, "Malaysia & Singapore") if reg
                             else "Malaysia & Singapore")
    fresh_wk = sum(1 for a in arts if a["ts"] and (NOW - a["ts"]).days < 7)
    st.markdown(f"<div class='sechead'>What's new &amp; good in "
                f"{where}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='kpi'>{fresh_wk} fresh this week · "
                f"{len(arts)} places · updated daily</div>", unsafe_allow_html=True)

    # quick actions: Surprise me + today's digest
    ac1, ac2, _ = st.columns([1.1, 1.3, 3])
    if ac1.button("Surprise me", use_container_width=True) and arts:
        # bias toward "good": authority or priority-1/2, prefer with a photo
        pool = [a for a in arts if a["source"] == "Authority"or a["priority"] <= 2] or arts
        withimg = [a for a in pool if a.get("image")]
        st.session_state.surprise = random.choice(withimg or pool)["url"]
    show_digest = ac2.toggle("Today's digest", value=False)

    if st.session_state.get("surprise"):
        pick = next((a for a in arts if a["url"] == st.session_state.surprise), None)
        if pick:
            st.markdown("<div class='sechead'>Tonight, try…</div>", unsafe_allow_html=True)
            render_card(pick, "surprise")

    if show_digest:
        with st.spinner("Building today's digest…"):
            md, _html, _a, _f = digest.build(region=reg, city=area.strip() or None,
                                             days=7, limit=10)
        with st.container(border=True):
            st.markdown(md)
        st.download_button("Download digest (.md)", md,
                           file_name=f"autonom-{NOW:%Y-%m-%d}.md",
                           mime="text/markdown")
        st.caption("Written daily to `digests/` by the scheduled job; add SMTP env "
                   "vars to also get it emailed (see README).")

    if not arts:
        st.info("Nothing here yet for this city. Widen the filter, or add a feed "
                "under **Add a source**.")
    else:
        # Featured Autonoms — top contributors + their recent posts
        feat = community.featured_contributors(arts, top=6)
        if feat:
            st.markdown("<div class='sechead'>Featured Autonoms</div>",
                        unsafe_allow_html=True)
            fcols = st.columns(2)
            for i, c in enumerate(feat):
                with fcols[i % 2].container(border=True):
                    tag = "member"if c["member"] else "creator"
                    st.markdown(f"**{c['name']}** · {c['count']} entries · {tag}")
                    for p in c["posts"]:
                        st.markdown(f"<div style='font-size:.82rem'>"
                                    f"<a href='{p['url']}' target='_blank'"
                                    f"style='color:{PAL['ink']}'>{p['title'][:70]}</a>"
                                    f"<span style='color:{PAL['muted']}'>· {ago(p['ts'])}"
                                    f"</span></div>", unsafe_allow_html=True)

        # ── the browse grid (rich, image-forward magazine cards) ────────────
        bcards, btotal = browse_cards(stats["total"], region, area, cuisine,
                                      acc_sel, price_sel, ft_sel, 24)
        st.markdown("<div class='sechead'>Browse the guide</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='kpi'>{btotal} places match · showing {len(bcards)} "
                    "· sorted by accolade &amp; rating</div>", unsafe_allow_html=True)
        if not bcards:
            st.info("No places match these filters — widen the region / cuisine up top.")
        else:
            render_grid(bcards, "browse", cols=3)


# ── tab: Makan Kaki (conversational recommender) ─────────────────────────────
REC_SYSTEM = ("You are Makan Kaki, a decisive local food concierge for Malaysia "
              "and Singapore. Using ONLY the numbered places provided (already "
              "ranked for the user), write 2-3 warm sentences that recommend the "
              "top one or two and say why — dish, accolade, vibe or value. Don't "
              "list every place (cards below do that) and never invent one.")


def _near_latlng():
    """Parse the sidebar 'Near me'box to (lat, lng), or None."""
    if not near_str.strip():
        return None
    try:
        la, ln = near_str.split(",")
        return (float(la), float(ln))
    except ValueError:
        return None


with chat_tab:
    st.session_state.setdefault("chat", [])

    if not st.session_state.chat:
        st.markdown("<div class='hero-h'>Makan Kaki</div>", unsafe_allow_html=True)
        st.markdown("<div class='hero-s'>Tell me a craving, an area or a budget — I'll "
                    "rank real places and give you the reason, contact and directions "
                    "for each.</div>", unsafe_allow_html=True)
        EXAMPLES = ["Best char kway teow in Penang", "Omakase under $250 in Singapore",
                    "Supper near Bangsar, open now", "Michelin Bib hawker in KL",
                    "Nasi lemak worth driving for"]
        ecols = st.columns(len(EXAMPLES))
        for c, ex in zip(ecols, EXAMPLES):
            if c.button(ex, key=f"ex_{ex}", use_container_width=True):
                st.session_state.pending_q = ex
                st.rerun()
    else:
        _c1, _c2 = st.columns([6, 1])
        if _c2.button("Clear", key="chat_clear"):
            st.session_state.chat = []
            st.rerun()

    open_now_only = st.toggle("Open now only", value=False, key="open_now",
                              help="Keep only places my best-effort hours parse says "
                                   "are open right now (advisory).")

    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"], unsafe_allow_html=True)

    prompt = (st.chat_input("Ask Makan Kaki for a recommendation…")
              or st.session_state.pop("pending_q", None))
    if prompt:
        st.session_state.chat.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            reg = None if region == "All"else region
            near = _near_latlng()
            with st.spinner("Finding the best places…"):
                hits = query.retrieve(prompt, k=48, region=reg,
                                      contains=[area, cuisine],
                                      embedder=_embedder(), coll=coll)
                hits = apply_facets(hits)
                # approximate proximity via the offline gazetteer (no geo in corpus)
                if near:
                    for h in hits:
                        loc = geo_gazetteer.locate(h["meta"])
                        if loc:
                            h["distance_km"] = query.haversine_km(
                                near[0], near[1], loc[0], loc[1])
                if open_now_only:
                    hits = [h for h in hits
                            if recommender.open_state(h["meta"].get("hours", "")) == "open"]
                recs = recommender.rank(hits, near=near, limit=6)
            if not recs:
                ans = ("I couldn't find a good match for that. Try widening the "
                       "region/facets in the sidebar"+
                       (", or turn off **Open now only**"if open_now_only else "") + ".")
                st.markdown(ans)
                st.session_state.chat.append({"role": "assistant", "content": ans})
            else:
                intro = None
                if query.has_api_key():
                    ctx = "\n\n".join(
                        f"[{i}] {h['meta'].get('title','')} — "
                        f"{h['meta'].get('accolades') or h['meta'].get('cuisine','')} "
                        f"in {h['meta'].get('city','')}. {h.get('doc','')[:200]}"
                        for i, h in enumerate(recs, 1))
                    try:
                        with st.spinner("Writing your recommendation…"):
                            intro = query.answer(prompt, ctx, system=REC_SYSTEM)
                    except Exception as e:
                        st.caption(f"(Live summary unavailable: {type(e).__name__})")
                if not intro:
                    where = area.strip() or (REGION_LABEL.get(reg, "") if reg else "")
                    loc_txt = f" near {where}" if where else ""
                    open_txt = " — open right now" if open_now_only else ""
                    intro = (f"Here are my top picks{loc_txt}, ranked by relevance, "
                             f"accolades and ratings{open_txt}:")
                html = (f"<div class='rec-intro'>{intro}</div>"
                        + "".join(rec_html(h, i) for i, h in enumerate(recs, 1)))
                st.markdown(html, unsafe_allow_html=True)
                st.session_state.chat.append({"role": "assistant", "content": html})


with find_tab:
    if "q"not in st.session_state:
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
        reg = None if region == "All"else region
        near = None
        if near_str.strip():
            try:
                _la, _ln = near_str.split(",")
                near = (float(_la), float(_ln))
            except ValueError:
                st.warning('“Near me” must look like `3.1390, 101.6869` — ignoring it.')
        with st.spinner("Searching…"):
            hits = query.retrieve(q, k=k * 4 if facets_active() else k, region=reg,
                                  contains=[area, cuisine], embedder=_embedder(),
                                  coll=coll, near=near,
                                  radius_km=radius_km if near else None)
            hits = apply_facets(hits)[:k]
        if near and not hits:
            st.info("No geo-tagged places within that radius. Widen the radius, or "
                    "run `enrich_geo.py` to add coordinates.")
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
                               f"{e}). Showing ranked snippets instead.")
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


# ── tab: Map (whole corpus on a map, filtered by sidebar) ────────────────────
def _rgb(hex_):
    hex_ = hex_.lstrip("#")
    return [int(hex_[i:i + 2], 16) for i in (0, 2, 4)]


with map_tab:
    st.markdown("<div class='sechead'>The list, on a map</div>",
                unsafe_allow_html=True)
    rows, unmapped = map_points(stats["total"], region, area, cuisine,
                                acc_sel, price_sel, ft_sel)
    n_mich = sum(1 for r in rows if r["acc_tier"] in facets._MICHELIN)
    st.markdown(
        f"<div class='kpi'>{len(rows)} places mapped · {n_mich} MICHELIN · "
        f"{unmapped} without a locatable area · pins are snapped to their "
        f"<b>district</b> (approximate), not exact addresses.</div>",
        unsafe_allow_html=True)

    if not rows:
        st.info("No places match the current filters. Loosen the region / facets "
                "in the sidebar.")
    else:
        try:
            import pandas as pd
            import pydeck as pdk
            ink = _rgb(PAL["ink"])
            for r in rows:
                mich = r["acc_tier"] in facets._MICHELIN
                r["radius"] = 130 if mich else 70
                r["color"] = ink + [235 if mich else 150]
                r["dish"] = r["cuisine"] or "—"
                r["acc_txt"] = r["accolades"] or (""if not mich else r["acc_tier"])
                r["addr_txt"] = f"{r['address']}"if r.get("address") else ""
                r["hrs_txt"] = f"{r['hours']}"if r.get("hours") else ""
            df = pd.DataFrame(rows)
            centers = {"MY": (3.14, 101.69, 9.2), "SG": (1.30, 103.84, 11),
                       "TH": (13.75, 100.52, 10.5)}
            if region in centers:
                lat0, lng0, zoom = centers[region]
            else:
                lat0 = sum(r["lat"] for r in rows) / len(rows)
                lng0 = sum(r["lng"] for r in rows) / len(rows)
                zoom = 5.2
            basemap = ("https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
                       if mode == "Dark"else
                       "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json")
            layer = pdk.Layer(
                "ScatterplotLayer", data=df, get_position="[lng, lat]",
                get_radius="radius", radius_min_pixels=4, radius_max_pixels=26,
                get_fill_color="color", get_line_color=[255, 255, 255],
                line_width_min_pixels=1, stroked=True, pickable=True)
            tooltip = {
                "html": "<b>{title}</b><br/>{dish} · {city} <i>({precision})</i>"
                        "<br/>{acc_txt}<br/>{addr_txt}<br/>{hrs_txt}",
                "style": {"backgroundColor": PAL["panel"], "color": PAL["ink"],
                          "border": f"1px solid {PAL['border']}",
                          "borderRadius": "10px", "fontSize": "12px",
                          "fontFamily": "Arial, sans-serif", "padding": "8px 10px"}}
            st.pydeck_chart(pdk.Deck(
                layers=[layer], map_style=basemap,
                initial_view_state=pdk.ViewState(
                    latitude=lat0, longitude=lng0, zoom=zoom, pitch=0),
                tooltip=tooltip), use_container_width=True)
            st.caption("Larger pins = MICHELIN listings. Hover a pin for details. "
                       "Districts are placed offline from a local gazetteer — no "
                       "geocoding API is called.")
        except Exception as e: # pragma: no cover — pydeck/tiles unavailable
            st.warning(f"Interactive map unavailable ({type(e).__name__}); showing a "
                       "basic pin map.")
            import pandas as pd
            st.map(pd.DataFrame([{"lat": r["lat"], "lon": r["lng"]} for r in rows]))

        # a compact list under the map (Michelin first, then by name)
        with st.expander(f"List these {len(rows)} places", expanded=False):
            for r in sorted(rows, key=lambda x: (x["acc_tier"] not in facets._MICHELIN,
                                                 x["title"].lower()))[:120]:
                star = ""if r["acc_tier"] in facets._MICHELIN else ""
                link = (f"[{r['title']}]({r['url']})"
                        if str(r["url"]).startswith("http") else r["title"])
                acc = f"· _{r['acc_tier']}_"if r["acc_tier"] else ""
                st.markdown(f"- {star}**{link}** — {r['dish'] if 'dish'in r else r['cuisine']}"
                            f"· {r['city'] or r['region']}{acc}")


# ── tab: My list (memory of your reviews) ────────────────────────────────────
def _apply_collections(url, key):
    personal.set_collections_for(url, st.session_state.get(key, []))


def render_saved(p, key):
    """A saved place with editable status / rating / note."""
    url = p.get("url", "")
    with st.container(border=True):
        c1, c2 = st.columns([1, 6], vertical_alignment="center")
        c1.markdown(_thumb(p.get("image", "")), unsafe_allow_html=True)
        rr = int(p.get("rating", 0))
        stars = f"{rr}/5"if rr else "unrated"
        c2.markdown(
            f"<div class='body'><h4><a href='{url}' target='_blank'"
            f"style='color:inherit;text-decoration:none'>{p.get('title','')}</a></h4>"
            f"<span class='badge badge-reg'>{REGION_LABEL.get(p.get('region',''), p.get('region','')) or '—'}</span>"
            f"<span class='badge'>{p.get('city','')}</span>"
            f"<span class='badge'>{p.get('source','')}</span>"
            f"<span class='stars'>&nbsp;{stars}</span></div>",
            unsafe_allow_html=True)
        e1, e2, e3 = st.columns([1.4, 1.6, 0.7], vertical_alignment="bottom")
        status = e1.radio("Status", ["want", "been"], horizontal=True,
                          index=0 if p.get("status", "want") == "want"else 1,
                          format_func=lambda s: "Want to go"if s == "want"else "Been",
                          key=f"stt_{key}")
        rating = e2.slider("My rating", 0, 5, int(p.get("rating", 0)), key=f"rt_{key}")
        remove = e3.button("", key=f"rm_{key}", help="Remove from list")
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
        if changed and st.button("Save review", key=f"sr_{key}", type="primary"):
            personal.upsert_place(url, status=status, rating=rating, note=note.strip())
            st.toast("Saved your review")
            st.rerun()


with mylist_tab:
    places = personal.load_places()
    if not places:
        st.info("Your list is empty. Tap ** Save** on any card in **Today** or "
                "**Find food**, then come back to rate it and jot a note.")
    else:
        want = [p for p in places if p.get("status", "want") == "want"]
        been = [p for p in places if p.get("status") == "been"]
        st.markdown(f"<div class='kpi'>{len(places)} saved · {len(been)} been · "
                    f"{len(want)} want to go</div>", unsafe_allow_html=True)

        # ── collections (named lists) ────────────────────────────────────────
        by_url = {p["url"]: p for p in places}
        cols = personal.load_collections()
        with st.expander(f"Collections ({len(cols)})", expanded=bool(cols)):
            nc1, nc2 = st.columns([3, 1])
            new_name = nc1.text_input("New collection", key="new_col",
                                      placeholder="e.g. Date night, Cheap eats, Omakase",
                                      label_visibility="collapsed")
            if nc2.button("Create", use_container_width=True) and new_name.strip():
                personal.create_collection(new_name.strip())
                st.rerun()
            for name, urls in cols.items():
                titles = [f"[{by_url[u]['title']}]({u})"for u in urls if u in by_url]
                cc1, cc2 = st.columns([5, 1])
                cc1.markdown(f"**{name}** ({len(titles)}) — "
                             + (" · ".join(titles) if titles else "_empty_"))
                if cc2.button("", key=f"delc_{name}", help=f"Delete '{name}'"):
                    personal.delete_collection(name)
                    st.rerun()
            if cols:
                st.caption("Assign a place to collections from its card below.")

        if want:
            st.markdown("<div class='sechead'>Want to go</div>", unsafe_allow_html=True)
            for i, p in enumerate(want):
                render_saved(p, f"want_{i}")
        if been:
            st.markdown("<div class='sechead'>Been there</div>", unsafe_allow_html=True)
            for i, p in enumerate(been):
                render_saved(p, f"been_{i}")

        # ── recommended for you (from your memory) ───────────────────────────
        st.markdown("<div class='sechead'>Recommended for you</div>",
                    unsafe_allow_html=True)
        seed_titles = [p.get("title", "") for p in places if p.get("title")][:6]
        if not seed_titles:
            st.caption("Save a few places and I'll suggest similar ones.")
        else:
            st.caption("Because you saved: "+ ", ".join(t[:30] for t in seed_titles[:3])
                       + ("…"if len(seed_titles) >3 else ""))
            with st.spinner("Finding places that match your taste…"):
                recs = query.retrieve("; ".join(seed_titles), k=12,
                                      embedder=_embedder(), coll=coll)
            saved_now = personal.saved_urls()
            fresh = [h for h in recs if h["meta"].get("url") not in saved_now][:6]
            if not fresh:
                st.caption("No new suggestions yet — add more sources to widen the pool.")
            for i, h in enumerate(fresh):
                render_card(card_from_hit(h), f"rec_{i}")


# ── tab: Contribute (write a dining review: photos, map, criteria scores) ────
REVIEW_TYPES = ["Hawker / Kopitiam", "Zi Char / Tai Chow", "Noodles", "Seafood",
                "Chinese", "Malay / Nasi", "Indian / Mamak", "Japanese / Omakase",
                "Korean", "Thai", "Cafe / Brunch", "Western", "Fine dining",
                "Dessert / Bakery", "Bar / Drinks", "Other"]
CRITERIA = [("food", "Food quality"), ("ambience", "Ambience"),
            ("service", "Service"), ("value", "Value for money")]


def review_scores_html(rv) -> str:
    """Overall + per-criterion score chips for a member review card."""
    sc = rv.get("scores") or {}
    overall = rv.get("overall") or rv.get("rating") or 0
    chips = [f"<span class='badge'>Overall {overall:g}/5</span>"]
    for key, label in CRITERIA:
        if sc.get(key):
            chips.append(f"<span class='badge'>{label} {sc[key]}/5</span>")
    return "<div class='chips'>" + "".join(chips) + "</div>"


PRICE_BANDS = ["Not sure", "Under RM20 per pax", "RM20–RM50 per pax",
               "RM50–RM100 per pax", "RM100–RM200 per pax", "Above RM200 per pax"]


def _map_preview(name, addr, city, region):
    """Live pin preview for the address the member is typing (offline gazetteer)."""
    loc = geo_gazetteer.locate({"address": addr, "city": city, "title": name,
                                "region": region})
    if not loc:
        return
    try:
        import pandas as pd
        import pydeck as pdk
        lat, lng, prec = loc
        basemap = ("https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
                   if mode == "Dark" else
                   "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json")
        st.pydeck_chart(pdk.Deck(
            map_style=basemap,
            initial_view_state=pdk.ViewState(latitude=lat, longitude=lng, zoom=13),
            layers=[pdk.Layer("ScatterplotLayer",
                              data=pd.DataFrame([{"lat": lat, "lng": lng}]),
                              get_position="[lng, lat]", get_radius=90,
                              get_fill_color=_rgb(PAL["ink"]) + [230],
                              radius_min_pixels=7, stroked=True,
                              get_line_color=[255, 255, 255], line_width_min_pixels=1)]),
            use_container_width=True)
        st.caption(f"Approximate pin — snapped to {prec} (offline gazetteer).")
    except Exception:
        pass


with contribute_tab:
    st.markdown("<div class='sechead'>Share a dining experience</div>",
                unsafe_allow_html=True)
    if not USER:
        st.info("Sign in (sidebar) to post your reviews as an **Autonom** "
                "— they'll appear in Today, Find and Featured.")
    else:
        if st.session_state.pop("rv_flash", None):
            st.success(st.session_state.pop("rv_flash_msg", "Posted."))
        st.caption(f"Posting as **{DISPLAY}**")

        # ── AI writing assistant (CHAIZEN-style) ─────────────────────────────
        with st.expander("✎ Autonom AI — turn your notes into a review draft"):
            st.caption("Jot the highlights, one per line. I'll write the review; "
                       "you can edit it below before posting.")
            notes = st.text_area("Highlights", key="rv_bullets", label_visibility="collapsed",
                                 placeholder="1. Good coffee\n2. Cheap breakfast set\n"
                                             "3. Nice decor\n4. Friendly service")
            if st.button("Generate draft", key="rv_gen"):
                if not notes.strip():
                    st.warning("Add a few highlights first.")
                else:
                    draft = None
                    if query.has_api_key():
                        with st.spinner("Writing your draft…"):
                            try:
                                draft = query.draft_review(
                                    notes, st.session_state.get("rv_name", ""))
                            except Exception as e:
                                st.warning(f"AI unavailable ({type(e).__name__}).")
                    if not draft:            # offline fallback: tidy bullets into prose
                        lines = [ln.strip("-•*0123456789. ").strip()
                                 for ln in notes.splitlines() if ln.strip()]
                        draft = " ".join(s if s.endswith((".", "!", "?")) else s + "."
                                         for s in lines)
                    st.session_state.rv_text = draft
                    st.rerun()

        # ── listing information ──────────────────────────────────────────────
        st.markdown("**Listing information**")
        rname = st.text_input("Restaurant name *", key="rv_name",
                              placeholder="e.g. Line Clear Nasi Kandar")
        r1, r2, r3 = st.columns(3)
        rregion = r1.selectbox("Region", ["MY", "SG", "TH", "ID", "PH", "VN",
                                          "KH", "LA", "MM", "BN"], key="rv_region")
        rcity = r2.text_input("City / area", key="rv_city",
                              placeholder="Penang / Bangsar")
        rtype = r3.selectbox("Category *", REVIEW_TYPES, key="rv_type")
        rexcerpt = st.text_input("Excerpt (one-line summary)", key="rv_excerpt",
                                 placeholder="the one-liner that sums it up")
        rtext = st.text_area("Your experience *", key="rv_text",
                             placeholder="What you ate, how it was, the vibe…")
        rorder = st.text_input("What to order", key="rv_order",
                               placeholder="the dish worth coming back for")

        # ── your scores ──────────────────────────────────────────────────────
        st.markdown("**Your scores** (1–5)")
        sc_cols = st.columns(len(CRITERIA))
        scores = {key: sc_cols[i].slider(label, 1, 5, 4, key=f"rv_sc_{key}")
                  for i, (key, label) in enumerate(CRITERIA)}
        st.caption(f"Overall so far: **{sum(scores.values())/len(scores):g}/5**")

        # ── pricing ──────────────────────────────────────────────────────────
        st.markdown("**Pricing**")
        rprice = st.radio("Price (per pax)", PRICE_BANDS, horizontal=True,
                          key="rv_price", label_visibility="collapsed")

        # ── contact ──────────────────────────────────────────────────────────
        st.markdown("**Contact**")
        cc1, cc2 = st.columns(2)
        rphone = cc1.text_input("Phone", key="rv_phone", placeholder="+60…")
        rweb = cc2.text_input("Website", key="rv_web", placeholder="https://…")

        # ── map ──────────────────────────────────────────────────────────────
        st.markdown("**Map**")
        mc1, mc2 = st.columns(2)
        rmaps = mc1.text_input("Google Maps link", key="rv_maps",
                               placeholder="paste a Google Maps URL")
        raddr = mc2.text_input("Address", key="rv_addr",
                               placeholder="listing address, e.g. Bangsar")
        _map_preview(rname, raddr, rcity, rregion)

        # ── media & menu ─────────────────────────────────────────────────────
        st.markdown("**Media & menu**")
        rphotos = st.file_uploader("Photos (max 5)", key="rv_photos",
                                   type=["jpg", "jpeg", "png", "webp"],
                                   accept_multiple_files=True)
        rvideo = st.text_input("Video — YouTube / Vimeo URL", key="rv_video",
                               placeholder="https://youtu.be/…")
        rmenu = st.text_input("Menu info", key="rv_menu",
                              placeholder="signatures, set prices, opening hours…")

        if st.button("Post review", type="primary", key="rv_post"):
            if not rname.strip() or not rtext.strip():
                st.warning("Restaurant name and your experience are required.")
            else:
                overall = round(sum(scores.values()) / len(scores), 1)
                maps = community.maps_link(rmaps or raddr, rname.strip(), rcity.strip())
                imgs = community.save_images(USER, rphotos)
                review = {"name": rname.strip(), "region": rregion,
                          "city": rcity.strip(), "type": rtype, "cuisine": rtype,
                          "excerpt": rexcerpt.strip(), "address": raddr.strip(),
                          "location": raddr.strip() or rcity.strip(), "maps": maps,
                          "scores": scores, "overall": overall,
                          "rating": round(overall), "stars": f"{overall:g}/5",
                          "price": "" if rprice == "Not sure" else rprice,
                          "phone": rphone.strip(), "website": rweb.strip(),
                          "order": rorder.strip(), "menu": rmenu.strip(),
                          "video": rvideo.strip(), "text": rtext.strip(),
                          "url": rweb.strip() or rvideo.strip(),
                          "images": imgs, "ts": NOW.isoformat()}
                review = personal.add_review(review)
                community.embed_review(review, USER, DISPLAY, coll, _embedder())
                for k in ("rv_name", "rv_city", "rv_excerpt", "rv_text", "rv_order",
                          "rv_phone", "rv_web", "rv_maps", "rv_addr", "rv_video",
                          "rv_menu", "rv_bullets", "rv_photos"):
                    st.session_state.pop(k, None)
                st.session_state.rv_flash = True
                st.session_state.rv_flash_msg = (
                    f"Posted — overall {overall:g}/5. Your review is live in Today, "
                    "Find and Makan Kaki. Thanks for contributing.")
                st.rerun()

        # your own reviews
        my = personal.load_reviews()
        st.markdown(f"<div class='sechead'>Your reviews ({len(my)})</div>",
                    unsafe_allow_html=True)
        for rv in my:
            with st.container(border=True):
                cimg, cbody = st.columns([1, 4], vertical_alignment="center")
                shots = [p for p in rv.get("images", []) if os.path.exists(p)]
                if shots:
                    cimg.image(shots[0], use_container_width=True)
                else:
                    cimg.markdown("<div class='thumb-wrap'><div class='thumb-ph'>"
                                  "</div></div>", unsafe_allow_html=True)
                contact = contact_html({"address": rv.get("address") or rv.get("location", ""),
                                        "phone": rv.get("phone", ""),
                                        "maps": rv.get("maps", "")})
                price_badge = (f"<span class='badge'>{rv['price']}</span>"
                               if rv.get("price") else "")
                excerpt = (f"<div class='snippet'><i>{rv['excerpt']}</i></div>"
                           if rv.get("excerpt") else "")
                cbody.markdown(
                    f"**{rv['name']}**"
                    f"<span class='badge badge-reg'>{REGION_LABEL.get(rv.get('region',''), rv.get('region',''))}</span>"
                    f"<span class='badge'>{rv.get('city','')}</span>"
                    f"<span class='badge'>{rv.get('type', rv.get('cuisine',''))}</span>"
                    f"{price_badge}{review_scores_html(rv)}{excerpt}"
                    f"<span class='snippet'>{rv.get('text','')[:240]}</span>{contact}",
                    unsafe_allow_html=True)
                if len(shots) > 1:
                    cbody.image(shots[1:4], width=90)
                if cbody.button("Delete", key=f"delrv_{rv['id']}"):
                    community.unembed_review(USER, rv["id"], coll)
                    personal.remove_review(rv["id"])
                    st.rerun()


# ── tab: add a source ────────────────────────────────────────────────────────
with add_tab:
    st.write("Ingest **your own** website, article, RSS feed, or sitemap into the "
             "same store. It goes through the identical polite pipeline — "
             "**robots.txt and rate limits are respected**, and bot detection is "
             "never bypassed.")

    with st.form("add_source"):
        url = st.text_input("URL", placeholder="https://example.com/feed/ or a single article URL")
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
        save = st.checkbox("Keep updated daily — add this feed to the scheduled "
                           "refresh", value=True,
                           help="Saves it to config/user_sources.yaml, which the "
                                "daily launchd/cron job re-ingests (priority 2).")
        submitted = st.form_submit_button("Add & ingest", type="primary")

    if submitted and url.strip():
        u = url.strip()
        src_label = label.strip() or (u.split("/")[2] if "://"in u else "User URL")
        with st.spinner(f"Fetching politely (≥{ingest.REQUEST_DELAY}s/host)…"):
            out = ingest.ingest_user_source(
                u, kind=kind, region=reg2, city=city2.strip(),
                source=src_label, url_filter=url_filter.strip(),
                limit=limit, embedder=_embedder(), coll=coll)
        added = out["added_chunks"]
        n_ok = sum(1 for r in out["results"] if r["status"] == "ok")
        n_blocked = sum(1 for r in out["results"] if r["status"] == "blocked")
        if added:
            st.success(f"Ingested **{n_ok}** article(s) **{added}** chunks "
                       f"(resolved as `{out['kind']}`). Re-running is idempotent.")
        else:
            st.warning(f"No new content ingested (resolved as `{out['kind']}`).")
        if n_blocked:
            st.info(f"{n_blocked} URL(s) skipped — disallowed by robots.txt.")

        if save:
            resolved = out["kind"]
            if resolved == "page":
                st.info("Saved as a single **page** — the daily job will re-check "
                        "just this one URL. Add the site's **feed** or **sitemap** "
                        "to keep pulling *new* posts.")
            entry = ingest.add_user_source(
                src_label, u, type=resolved, region=reg2, city=city2.strip(),
                priority=2, url_filter=url_filter.strip())
            st.success(f"Saved to the daily refresh as **{entry['name']}** "
                       f"(`{entry['type']}`, priority {entry['priority']}).")

        with st.expander("Per-URL detail"):
            for r in out["results"]:
                icon = {"ok": "", "blocked": "", "fetch_failed": "",
                        "too_short": "", "no_url": ""}.get(r["status"], "•")
                st.write(f"{icon} `{r['status']}` — {r.get('title') or r['url']} "
                         f"({r['chunks']} chunks)")
        st.caption("Switch to **Find food** — your new content is searchable now.")

    # currently-saved user feeds (part of the daily refresh)
    user_srcs = ingest.load_user_sources()
    st.divider()
    st.markdown(f"** Your saved feeds — refreshed daily ({len(user_srcs)})**")
    if not user_srcs:
        st.caption("None yet. Add a feed or sitemap above and keep "
                   "“Keep updated daily” ticked.")
    else:
        for s in user_srcs:
            st.markdown(
                f"- **{s.get('name','')}** · `{s.get('type','')}` · "
                f"{REGION_LABEL.get(s.get('region',''), s.get('region','') or '—')} · "
                f"priority {s.get('priority','')} \n"
                f"<span style='color:{PAL['muted']};font-size:.82rem'>{s.get('url','')}</span>",
                unsafe_allow_html=True)
        st.caption("Managed in `config/user_sources.yaml`. The scheduled job runs "
                   "`ingest.py --min-priority 2`, so priority-1&2 feeds refresh daily.")

    # ── add a Michelin / authority pick (e.g. from the printed guide) ────────
    st.divider()
    st.markdown("### Add a Michelin / authority pick")
    st.caption("For entries from a printed guide (e.g. the MICHELIN Guide KL/Penang) "
               "or Asia's 50 Best. These are **curated facts, never scraped** — "
               "enter name / stars / cuisine only, not the guide's review text. "
               "They show under **Today Michelin & authority picks**.")
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
        asub = st.form_submit_button("Add pick", type="primary")
    if asub:
        if not an.strip():
            st.warning("A restaurant name is required.")
        else:
            row = {"name": an.strip(), "city": acity.strip(), "region": areg,
                   "stars": astars, "cuisine": acuisine.strip(),
                   "url": aurl.strip(), "note": anote.strip()}
            authority.append_csv(row)
            authority.add_rows([row], embedder=_embedder(), coll=coll)
            st.success(f"Added **{row['name']}**"
                       + (f"({astars})"if astars else "")
                       + "to your authority picks and saved it to "
                       "`config/curated_authority.csv`.")

    try:
        n_auth = len(coll.get(where={"source": "Authority"})["ids"])
    except Exception:
        n_auth = 0
    st.caption(f"You currently have **{n_auth}** authority picks. Bulk-edit them any "
               "time in `config/curated_authority.csv`, then rerun "
               "`python curate_authority.py`.")

    # ── Instagram sources (official Graph API — no scraping) ─────────────────
    import json as _json
    import subprocess as _sp
    IG_CONF = os.path.expanduser("~/eatlist/config/instagram.json")
    IG_SECRET = os.path.expanduser("~/eatlist/config/instagram_secret.json")
    IG_PY = os.path.expanduser("~/eatlist/.venv/bin/python")
    IG_SCRIPT = os.path.expanduser("~/eatlist/scripts/ingest_instagram.py")

    st.divider()
    st.markdown("### Instagram sources (Graph API)")
    st.caption("Auto-ingest recent posts from **Business/Creator** food-blogger "
               "handles and hashtags via Instagram's **official Graph API** — "
               "captions are parsed into the Master List, then synced to Autonom. "
               "No scraping, ToS-respecting. A daily job runs at 06:00; you can "
               "also run it on demand below.")

    connected = bool(os.environ.get("IG_ACCESS_TOKEN", "").strip()
                     or os.path.exists(IG_SECRET))
    if connected:
        st.success("Graph API token detected — ingestion is live.")
    else:
        st.info("Not connected yet (the daily job stays dormant). Add a long-lived "
                "token + IG user id to `~/eatlist/config/instagram_secret.json` "
                '(`{"access_token": "…", "user_id": "…"}`) to switch it on. '
                "Setup steps are in `scripts/ingest_instagram.py`.")

    try:
        _ig = _json.load(open(IG_CONF)) if os.path.exists(IG_CONF) else {}
    except (ValueError, OSError):
        _ig = {}
    with st.form("ig_sources"):
        ig_handles = st.text_area(
            "Handles (one per line, Business/Creator accounts only)",
            value="\n".join(_ig.get("handles", [])),
            placeholder="eatdrinkkl\nklfoodie\nmalaysiafoodandtravel")
        ig_tags = st.text_area("Hashtags (one per line, without #)",
                               value="\n".join(_ig.get("hashtags", [])),
                               placeholder="klfood\npenangfood")
        ig_n = st.slider("Max posts per source", 1, 25,
                         int(_ig.get("max_per_source", 10)))
        if st.form_submit_button("Save Instagram sources", type="primary"):
            os.makedirs(os.path.dirname(IG_CONF), exist_ok=True)
            out = {"handles": [h.strip().lstrip("@") for h in ig_handles.splitlines()
                               if h.strip()],
                   "hashtags": [t.strip().lstrip("#") for t in ig_tags.splitlines()
                                if t.strip()],
                   "max_per_source": ig_n}
            _json.dump(out, open(IG_CONF, "w"), indent=2)
            st.success(f"Saved {len(out['handles'])} handle(s) and "
                       f"{len(out['hashtags'])} hashtag(s).")

    if st.button("Run Instagram sync now", disabled=not connected):
        with st.spinner("Fetching from the Graph API & merging…"):
            p = _sp.run([IG_PY, IG_SCRIPT, "--merge"],
                        cwd=os.path.expanduser("~/eatlist"),
                        capture_output=True, text=True, timeout=600)
        st.code((p.stdout or "") + (p.stderr or ""), language="text")
        if p.returncode == 0:
            with st.spinner("Syncing new posts into the Autonom core…"):
                import import_eatlist
                import_eatlist.run(os.path.expanduser(
                    "~/eatlist/Asia_Eateries_Master_List.xlsx"))
            load_articles.clear()
            st.success("Done — new Instagram posts are live in Autonom.")
        else:
            st.error("The Graph API run failed — see the log above.")
