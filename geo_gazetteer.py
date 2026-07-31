#!/usr/bin/env python3
"""
Offline gazetteer: resolve a place's area / city text to an approximate
lat-lng so the Map tab works with zero network calls (no geocoding API, fits
the project's local-only ethos). Coordinates are *area centroids*, not exact
addresses — a place is snapped to its district, then given a small deterministic
jitter (hashed from its name) so venues in the same district fan out instead of
stacking on one pin. `locate()` reports the precision it achieved so the UI can
label the map honestly.
"""
import hashlib

# district / neighbourhood centroids — matched as substrings of address+city+title.
# Longest keys are tried first, so "petaling jaya" wins over "penang" etc.
AREAS = {
    # ── Klang Valley (KL + Selangor) ──
    "kuala lumpur": (3.1470, 101.6990), "bukit bintang": (3.1460, 101.7110),
    "bangsar": (3.1280, 101.6780), "mont kiara": (3.1710, 101.6500),
    "petaling jaya": (3.1070, 101.6060), "ara damansara": (3.1080, 101.5830),
    "damansara heights": (3.1500, 101.6620), "kota damansara": (3.1560, 101.5880),
    "ss2": (3.1170, 101.6230), "ss15": (3.0760, 101.5850),
    "subang jaya": (3.0430, 101.5800), "subang": (3.0430, 101.5800),
    "shah alam": (3.0730, 101.5180), "puchong": (3.0260, 101.6160),
    "cheras": (3.0860, 101.7510), "kepong": (3.2130, 101.6350),
    "setapak": (3.1970, 101.7260), "ampang": (3.1500, 101.7600),
    "klang": (3.0440, 101.4450), "kajang": (2.9930, 101.7880),
    "semenyih": (2.9550, 101.8430), "sungai buloh": (3.2060, 101.5770),
    "sri petaling": (3.0660, 101.6880), "old klang road": (3.1000, 101.6770),
    "salak selatan": (3.0930, 101.7020), "salak south": (3.0930, 101.7020),
    "pudu": (3.1350, 101.7100), "imbi": (3.1430, 101.7120),
    "brickfields": (3.1280, 101.6860), "ttdi": (3.1450, 101.6300),
    "taman tun": (3.1450, 101.6300), "seri kembangan": (3.0230, 101.7060),
    "dataran ara damansara": (3.1080, 101.5830), "batu belah": (3.0300, 101.4590),
    # ── Penang ──
    "george town": (5.4140, 100.3290), "georgetown": (5.4140, 100.3290),
    "air itam": (5.3990, 100.2760), "ayer itam": (5.3990, 100.2760),
    "bayan lepas": (5.2940, 100.2710), "butterworth": (5.3990, 100.3630),
    "balik pulau": (5.3510, 100.2330), "gurney": (5.4380, 100.3090),
    "penang": (5.4140, 100.3290),
    # ── other Malaysia ──
    "ipoh": (4.5970, 101.0900), "melaka": (2.1960, 102.2500),
    "malacca": (2.1960, 102.2500), "johor bahru": (1.4920, 103.7410),
    "seremban": (2.7250, 101.9420), "kota kinabalu": (5.9800, 116.0730),
    "kuching": (1.5530, 110.3590), "kuantan": (3.8070, 103.3260),
    # ── Singapore ──
    "chinatown": (1.2830, 103.8440), "tanjong pagar": (1.2760, 103.8460),
    "tiong bahru": (1.2860, 103.8270), "geylang": (1.3180, 103.8870),
    "joo chiat": (1.3100, 103.9000), "katong": (1.3050, 103.9050),
    "east coast": (1.3060, 103.9210), "bedok": (1.3240, 103.9300),
    "toa payoh": (1.3340, 103.8470), "ang mo kio": (1.3700, 103.8490),
    "jurong": (1.3330, 103.7420), "clementi": (1.3150, 103.7650),
    "bukit timah": (1.3290, 103.8020), "serangoon": (1.3500, 103.8730),
    "hougang": (1.3710, 103.8920), "tampines": (1.3530, 103.9450),
    "woodlands": (1.4370, 103.7860), "yishun": (1.4290, 103.8350),
    "orchard": (1.3040, 103.8320), "marina bay": (1.2820, 103.8580),
    "little india": (1.3070, 103.8490), "kallang": (1.3110, 103.8710),
    "novena": (1.3200, 103.8440), "holland village": (1.3110, 103.7960),
    "dempsey": (1.3040, 103.8100), "boat quay": (1.2880, 103.8470),
    "clarke quay": (1.2900, 103.8460), "bugis": (1.3000, 103.8560),
    "paya lebar": (1.3180, 103.8930), "punggol": (1.4050, 103.9020),
    "sentosa": (1.2490, 103.8300), "raffles place": (1.2840, 103.8510),
    # ── Bangkok ──
    "thonglor": (13.7350, 100.5830), "thong lo": (13.7350, 100.5830),
    "sukhumvit": (13.7380, 100.5600), "yaowarat": (13.7410, 100.5100),
    "ari": (13.7790, 100.5450), "bangkok": (13.7560, 100.5010),
}

# coarse fall-backs keyed on the `city` field, then the `region` code
CITY = {
    "kuala lumpur": (3.1470, 101.6990), "singapore": (1.2900, 103.8510),
    "selangor": (3.0730, 101.5180), "penang": (5.4140, 100.3290),
    "bangkok": (13.7560, 100.5010), "melaka": (2.1960, 102.2500),
    "perak": (4.5970, 101.0900), "johor": (1.4920, 103.7410),
    "johor bahru": (1.4920, 103.7410), "petaling jaya": (3.1070, 101.6060),
    "sabah": (5.9800, 116.0730), "pahang": (3.8070, 103.3260),
    "negeri sembilan": (2.7250, 101.9420),
}
REGION = {"MY": (3.1470, 101.6990), "SG": (1.2900, 103.8510),
          "TH": (13.7560, 100.5010)}

_AREA_KEYS = sorted(AREAS, key=len, reverse=True)   # most specific first


def _jitter(name: str):
    """Deterministic ±~1.2 km offset from a name, so same-district pins spread."""
    h = hashlib.sha1(name.encode("utf-8")).digest()
    dx = (h[0] / 255 - 0.5) * 0.022
    dy = (h[1] / 255 - 0.5) * 0.022
    return dx, dy


def locate(meta):
    """Return (lat, lng, precision) or None. precision ∈ {'area','city','region'}.
    Snaps to a district/city centroid + a deterministic jitter."""
    hay = " ".join(str(meta.get(k, "") or "") for k in
                   ("address", "city", "title", "cuisine")).lower()
    base, precision = None, None
    for key in _AREA_KEYS:
        if key in hay:
            base, precision = AREAS[key], "area"
            break
    if base is None:
        city = (meta.get("city") or "").strip().lower()
        if city in CITY:
            base, precision = CITY[city], "city"
    if base is None:
        base = REGION.get((meta.get("region") or "").strip().upper())
        precision = "region" if base else None
    if base is None:
        return None
    dx, dy = _jitter(meta.get("title", "") or hay[:24])
    return (round(base[0] + dx, 5), round(base[1] + dy, 5), precision)
