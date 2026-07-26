#!/usr/bin/env python3
"""
ChiefEpicure daily digest — "what's new & good" as a file you can read or email.

Generates a Markdown + HTML digest from the current corpus (authority picks +
the freshest finds), writes it to ./digests/, and — only if SMTP is configured
via environment variables — emails it to you.

    python digest.py                       # write ./digests/YYYY-MM-DD.md + .html
    python digest.py --region MY --days 7  # scope + freshness window
    python digest.py --email               # also email (needs env vars below)

Email is OFF unless ALL of these are set (nothing is ever hard-coded):
    DIGEST_SMTP_HOST, DIGEST_SMTP_PORT, DIGEST_SMTP_USER,
    DIGEST_SMTP_PASS, DIGEST_EMAIL_FROM, DIGEST_EMAIL_TO
The digest is sent from/to your own account — this script never invents
recipients and stores no secrets.
"""
import argparse
import datetime as dt
import os

import ingest
import util

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ORANGE, GREEN, INK, MUTED = "#fa8b0c", "#28a800", "#252525", "#5b6470"


def gather(region=None, city=None, days=7, limit=10):
    coll = ingest.get_collection()
    arts = util.load_articles(coll)
    if region:
        arts = [a for a in arts if a["region"] == region]
    if city:
        arts = [a for a in arts if city.lower() in (a["city"] or "").lower()]
    authority = [a for a in arts if a["source"] == "Authority"][:5]
    now = dt.datetime.now(dt.timezone.utc)
    fresh = [a for a in arts
             if a["ts"] and (now - a["ts"]).days < days and a["source"] != "Authority"]
    return authority, fresh[:limit]


def to_markdown(authority, fresh, region, city, days) -> str:
    where = city or {"MY": "Malaysia", "SG": "Singapore"}.get(region, "Malaysia & Singapore")
    today = dt.datetime.now(dt.timezone.utc).strftime("%A, %d %b %Y")
    L = [f"# 🍜 ChiefEpicure — what's new & good in {where}", f"_{today}_", ""]
    if authority:
        L.append("## ⭐ Michelin & authority picks")
        for a in authority:
            L.append(f"- **[{a['title']}]({a['url']})** — {a['text'][:120].strip()}")
        L.append("")
    L.append(f"## 🆕 Fresh finds (last {days} days)")
    if not fresh:
        L.append("_Nothing new in this window — check back tomorrow._")
    for a in fresh:
        when = util.ago(a["ts"])
        L.append(f"- **[{a['title']}]({a['url']})** · _{a['source']} · {when}_  \n"
                 f"  {a['text'][:160].strip()}…")
    L.append("")
    L.append("—\n*Grounded in your ingested food-blog reviews. Sources linked above.*")
    return "\n".join(L)


def to_html(authority, fresh, region, city, days) -> str:
    where = city or {"MY": "Malaysia", "SG": "Singapore"}.get(region, "Malaysia & Singapore")
    today = dt.datetime.now(dt.timezone.utc).strftime("%A, %d %b %Y")

    def card(a, tag=""):
        img = (f'<img src="{a["image"]}" width="88" height="88" '
               f'style="border-radius:8px;object-fit:cover;float:left;margin:0 12px 6px 0">'
               if a.get("image") else "")
        meta = tag or f'{a["source"]} · {util.ago(a["ts"])}'
        return (f'<div style="overflow:hidden;border:1px solid #ededed;border-left:4px solid '
                f'{ORANGE};border-radius:10px;padding:12px 14px;margin:0 0 12px">{img}'
                f'<a href="{a["url"]}" style="color:{INK};text-decoration:none;font-weight:700;'
                f'font-size:16px">{a["title"]}</a>'
                f'<div style="color:{MUTED};font-size:12px;margin:3px 0 6px">{meta}</div>'
                f'<div style="color:{MUTED};font-size:13px;line-height:1.5">'
                f'{a["text"][:180].strip()}…</div></div>')

    parts = [f'<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:640px;'
             f'margin:auto;color:{INK}">',
             f'<h1 style="font-size:24px">🍜 <span style="color:{ORANGE}">Chief</span>'
             f'Epicure<span style="color:{GREEN}">.</span></h1>',
             f'<p style="color:{MUTED};margin-top:-8px">What\'s new &amp; good in '
             f'{where} — {today}</p>']
    if authority:
        parts.append(f'<h2 style="font-size:17px">⭐ Michelin &amp; authority picks</h2>')
        parts += [card(a, tag=a["source"]) for a in authority]
    parts.append(f'<h2 style="font-size:17px">🆕 Fresh finds (last {days} days)</h2>')
    if not fresh:
        parts.append(f'<p style="color:{MUTED}">Nothing new in this window.</p>')
    parts += [card(a) for a in fresh]
    parts.append(f'<p style="color:{MUTED};font-size:12px">Grounded in your ingested '
                 f'reviews. Sources linked above.</p></div>')
    return "\n".join(parts)


def write_files(md: str, html: str, outdir="digests") -> str:
    os.makedirs(outdir, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    base = os.path.join(outdir, stamp)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    with open(base + ".html", "w", encoding="utf-8") as f:
        f.write(html)
    return base


def maybe_email(subject: str, html: str) -> str:
    """Email the digest only if all SMTP env vars are set. Returns a status."""
    keys = ("DIGEST_SMTP_HOST", "DIGEST_SMTP_PORT", "DIGEST_SMTP_USER",
            "DIGEST_SMTP_PASS", "DIGEST_EMAIL_FROM", "DIGEST_EMAIL_TO")
    cfg = {k: os.getenv(k) for k in keys}
    if not all(cfg.values()):
        missing = [k for k in keys if not cfg[k]]
        return f"email skipped — not configured (missing: {', '.join(missing)})"
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["DIGEST_EMAIL_FROM"]
    msg["To"] = cfg["DIGEST_EMAIL_TO"]
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(cfg["DIGEST_SMTP_HOST"], int(cfg["DIGEST_SMTP_PORT"]), timeout=30) as s:
            s.starttls()
            s.login(cfg["DIGEST_SMTP_USER"], cfg["DIGEST_SMTP_PASS"])
            s.sendmail(cfg["DIGEST_EMAIL_FROM"], [cfg["DIGEST_EMAIL_TO"]], msg.as_string())
        return f"emailed to {cfg['DIGEST_EMAIL_TO']}"
    except Exception as e:
        return f"email failed: {type(e).__name__}: {e}"


def build(region=None, city=None, days=7, limit=10):
    authority, fresh = gather(region, city, days, limit)
    md = to_markdown(authority, fresh, region, city, days)
    html = to_html(authority, fresh, region, city, days)
    return md, html, authority, fresh


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate the ChiefEpicure daily digest.")
    ap.add_argument("--region", choices=["SG", "MY"], default=None)
    ap.add_argument("--city", default=None)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--email", action="store_true", help="also email (if SMTP env set)")
    args = ap.parse_args()

    md, html, authority, fresh = build(args.region, args.city, args.days, args.limit)
    base = write_files(md, html)
    print(f"Wrote {base}.md and {base}.html "
          f"({len(authority)} authority + {len(fresh)} fresh).")
    if args.email:
        where = args.city or args.region or "MY & SG"
        subject = f"🍜 ChiefEpicure — what's new & good ({where})"
        print(maybe_email(subject, html))
