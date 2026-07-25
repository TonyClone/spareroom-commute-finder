from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from flatfinder.geo.distance import haversine_km
from flatfinder.prices import parse_price, pw_to_pcm

KM_PER_MILE = 1.609344

# Outward postcode area (letters only), e.g. "SW", "E", "CR"
_AREA_RE = re.compile(r"^([A-Z]{1,2})", re.I)
_OUTCODE_RE = re.compile(r"^([A-Z]{1,2}\d{1,2}[A-Z]?)", re.I)
# A FULL compact postcode: outcode + inward (digit + two letters). Anchored so
# the outcode can be split off without the inward's leading digit bleeding in.
_FULL_POSTCODE_RE = re.compile(r"^([A-Z]{1,2}\d{1,2}[A-Z]?)(\d[A-Z]{2})$", re.I)
# SpareRoom search cards: "Forest Hill (SE23)" or "(N17)"
_PAREN_OUTCODE_RE = re.compile(
    r"(?:^|[^\w])(?:([A-Za-z][A-Za-z\s\-']{1,40}?)\s+)?\(([A-Z]{1,2}\d{1,2}[A-Z]?)\)",
    re.I,
)
_PRICED_RE = re.compile(
    r"£\s*([\d,]+(?:\.\d+)?)\s*(?:-\s*£?\s*[\d,]+(?:\.\d+)?)?\s*(pcm|pw|pm|p/?w|per\s*week|per\s*month)?",
    re.I,
)

# Hard reject: almost never ≤30 min door-to-door public transport to central Soho/W1.
FAR_OUTCODE_PREFIXES: tuple[str, ...] = (
    # Outer / commuter south & east
    "CR", "BR", "DA", "RM", "SM", "KT", "TN", "ME", "CT", "SS", "CM",
    # North / Herts fringe
    "EN", "WD", "AL", "SG", "LU", "HP",
    # West fringe / airport / Thames Valley
    "SL", "RG", "GU", "RH", "TW",
    # Far NW
    "HA9", "HA8", "HA7", "HA6", "HA5",
    "UB",
    "IG",
    # Outer SE districts that routinely blow 30 min to Soho
    "SE20", "SE21", "SE22", "SE23", "SE25", "SE26", "SE27", "SE19", "SE18",
    "SE9", "SE12", "SE6",
    # Outer E
    "E4", "E6", "E7", "E10", "E11", "E12", "E13", "E18",
    # Outer N
    "N9", "N11", "N13", "N14", "N17", "N18", "N21", "N22",
    # Outer NW
    "NW7", "NW9",
    # Outer SW (far)
    "SW16", "SW17", "SW19", "SW20",
    # Outer W
    "W3", "W4", "W5", "W7", "W13",  # Acton/Ealing often >30 door-to-door peak
)

# Place-name tokens that almost never work for ≤30 min to Soho (search-card early skip).
# Matched as whole words against title/snippet (case-insensitive).
FAR_PLACE_NAMES: frozenset[str] = frozenset(
    {
        "croydon", "bromley", "orpington", "sidcup", "bexley", "bexleyheath",
        "dartford", "gravesend", "romford", "ilford", "barking", "dagenham",
        "upminster", "hornchurch", "rainham",
        "enfield", "edmonton", "ponders end", "waltham cross", "cheshunt",
        "watford", "st albans", "hatfield", "hemel", "luton",
        "uxbridge", "heathrow", "hounslow", "feltham", "hayes", "southall",
        "hillingdon", "ruislip", "pinner", "northwood",
        "kingston", "surbiton", "epsom", "ewell", "sutton", "cheam", "carshalton",
        "mitcham", "morden", "worcester park",
        "crystal palace", "norwood", "thornton heath", "norbury", "purley",
        "coulsdon", "caterham", "redhill", "reigate", "guildford", "woking",
        "slough", "windsor", "maidenhead", "reading",
        "romford", "chadwell heath", "goodmayes",
        "woodford", "chingford", "loughton", "epping",
        "barnet", "edgware", "stanmore", "harrow weald",
        "richmond", "twickenham", "teddington", "hampton",
        "woolwich", "charlton", "eltham",
        "catford",
        "forest hill", "honor oak", "sydenham", "penge", "beckenham",
        "streatham",
        "tottenham", "wood green",
        "walthamstow",
        # Note: stratford, clapham, brixton, lewisham, greenwich, tooting, wimbledon
        # are intentionally NOT listed — too easy to false-negative; outcode rules handle outer ones.
    }
)

# Explicit keep hints: if present, do not early-skip on place name alone
CENTRALISH_HINTS = frozenset(
    {
        "zone 1", "zone1", "soho", "fitzrovia", "shoreditch", "hackney", "dalston",
        "islington", "angel", "clerkenwell", "holborn", "bloomsbury", "covent garden",
        "camden", "kentish town", "highbury", "canonbury", "bethnal green",
        "whitechapel", "aldgate", "liverpool street", "london bridge", "borough",
        "bermondsey", "elephant", "vauxhall", "pimlico", "victoria", "westminster",
        "marylebone", "paddington", "bayswater", "notting hill", "shepherds bush",
        "shepherd's bush", "hammersmith", "fulham", "chelsea", "kensington",
        "earls court", "earl's court", "battersea", "clapham", "brixton",
        "stockwell", "oval", "kennington", "peckham", "new cross", "deptford",
        "canary wharf", "limehouse", "mile end", "bow", "homerton", "hackney wick",
        "stratford", "canada water", "surrey quays", "rotherhithe",
    }
)

SLOW_AREAS = frozenset({"SE", "BR", "CR", "SM", "DA", "RM", "EN", "IG", "HA", "UB", "KT", "TW"})
FAST_AREAS = frozenset({"E", "EC", "N", "NW", "W", "WC", "SW"})


@dataclass
class PrefilterResult:
    skip: bool
    reason: str = ""
    distance_km: float | None = None
    est_minutes: float | None = None
    outcode: str | None = None
    stage: str = ""  # search | detail | tfl


def extract_outcode(postcode: str | None) -> str | None:
    if not postcode:
        return None
    pc = postcode.strip().upper().replace(" ", "")
    # Full postcodes must have the inward code split off structurally, not by a
    # greedy prefix match: "N1 7GU" compacts to "N17GU", and a bare prefix grab
    # would read "N17G" — putting an Angel room on the N17 (Tottenham) denylist.
    m = _FULL_POSTCODE_RE.match(pc)
    if m:
        return m.group(1)
    m = _OUTCODE_RE.match(pc)
    return m.group(1) if m else None


def extract_area(outcode: str | None) -> str | None:
    if not outcode:
        return None
    m = _AREA_RE.match(outcode)
    return m.group(1).upper() if m else None


def parse_card_location(text: str) -> tuple[str | None, str | None]:
    """
    From search card text return (area_name, outcode).
    e.g. 'Forest Hill (SE23) £780 pcm' → ('Forest Hill', 'SE23')
    """
    if not text:
        return None, None
    # Prefer last parenthetical outcode near a place name (cards put location mid-snippet)
    matches = list(_PAREN_OUTCODE_RE.finditer(text))
    if not matches:
        # bare full postcode
        from flatfinder.geo.postcodes import extract_postcode

        pc = extract_postcode(text)
        return None, extract_outcode(pc) if pc else None
    m = matches[-1]
    name = (m.group(1) or "").strip() or None
    outcode = m.group(2).upper()
    return name, outcode


def parse_card_price(text: str) -> tuple[float | None, float | None, str]:
    """
    Prefer real rent (£750 pcm) over junk (£9 photos).
    Returns (pcm, pw, raw).
    """
    if not text:
        return None, None, ""
    ranked: list[tuple[float, float, float, str]] = []
    for m in _PRICED_RE.finditer(text):
        value = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower().replace(" ", "")
        raw = m.group(0).strip()
        if unit in {"pw", "p/w", "perweek"}:
            pcm, pw = pw_to_pcm(value), value
        elif unit in {"pcm", "pm", "permonth"}:
            pcm, pw = value, round(value * 12 / 52, 2)
        else:
            if value < 50:
                continue
            if value <= 800:
                pcm, pw = pw_to_pcm(value), value
            else:
                pcm, pw = value, round(value * 12 / 52, 2)
        score = float(pcm)
        if unit in {"pcm", "pm", "permonth", "pw", "p/w", "perweek"}:
            score += 10_000
        if 400 <= pcm <= 3000:
            score += 5_000
        ranked.append((score, pcm, pw, raw))
    if not ranked:
        return parse_price(text)
    ranked.sort(key=lambda x: x[0], reverse=True)
    _, pcm, pw, raw = ranked[0]
    return pcm, pw, raw


def outcode_is_hard_far(outcode: str | None) -> bool:
    if not outcode:
        return False
    oc = outcode.upper().replace(" ", "")
    for prefix in sorted(FAR_OUTCODE_PREFIXES, key=len, reverse=True):
        if oc.startswith(prefix):
            return True
    return False


def place_name_is_hard_far(text: str) -> str | None:
    """Return matched far place name or None. Skip if strong central hint present."""
    if not text:
        return None
    low = " ".join(text.lower().split())
    for hint in CENTRALISH_HINTS:
        if hint in low:
            return None
    # longer names first
    for name in sorted(FAR_PLACE_NAMES, key=len, reverse=True):
        # word-boundary-ish
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", low):
            return name
    return None


def _bearing_sector(dlat: float, dlon: float) -> str:
    import math

    ang = math.degrees(math.atan2(dlon, dlat))
    if ang < 0:
        ang += 360
    if 45 <= ang < 135:
        return "E"
    if 135 <= ang < 225:
        return "S"
    if 225 <= ang < 315:
        return "W"
    return "N"


def estimate_pt_minutes(
    distance_km: float,
    *,
    area: str | None = None,
    sector: str | None = None,
) -> float:
    overhead = 10.0
    min_per_km = 2.4
    if area in SLOW_AREAS or sector == "S":
        min_per_km = 3.2
        overhead = 12.0
    elif area in FAST_AREAS and sector in {"E", "N", "W"}:
        min_per_km = 2.1
        overhead = 9.0
    return overhead + distance_km * min_per_km


# Fastest-corridor PT coefficients (best case of estimate_pt_minutes: fast area,
# outbound sector). Used to derive the SpareRoom search radius so the geographic
# net is at least as generous as the finest thing the local prefilter would keep.
_PT_FAST_OVERHEAD_MIN = 9.0
_PT_FAST_MIN_PER_KM = 2.1


def radius_km_for_minutes(max_minutes: float, estimate_slack: float = 1.35) -> float:
    """Crow-flies km reachable within the prefilter's keep-threshold.

    Inverts the fastest-corridor PT estimate at ``max_minutes * estimate_slack``
    — the exact ceiling ``should_skip_tfl`` uses — so the search radius and the
    local prefilter agree: the net admits everything the pipeline might keep,
    and never silently drops a fast-line listing before we can TfL it.
    """
    reach = (max_minutes * estimate_slack - _PT_FAST_OVERHEAD_MIN) / _PT_FAST_MIN_PER_KM
    return max(1.0, reach)


def radius_miles_for_minutes(
    max_minutes: float,
    estimate_slack: float = 1.35,
    *,
    min_miles: int = 2,
    max_miles: int = 40,
) -> int:
    """Whole-mile SpareRoom radius (`miles_from_max`) derived from commute budget.

    Scales with the commute: a tighter max_minutes shrinks the net, a looser one
    grows it. SpareRoom's control tops out at 40 miles.
    """
    miles = radius_km_for_minutes(max_minutes, estimate_slack) / KM_PER_MILE
    return max(min_miles, min(max_miles, math.ceil(miles)))


def should_skip_search_card(
    card: dict[str, Any],
    *,
    max_pcm: float,
    max_minutes: int = 30,
    hard_max_km: float = 22.0,
    estimate_slack: float = 1.35,
    office_lat: float | None = None,
    office_lon: float | None = None,
) -> PrefilterResult:
    """
    Early filter on SpareRoom *search card* data — before detail scrape.

    Signals: price, (outcode) in title/snippet, place names, optional lat/lon if present.
    Fail-open (skip=False) when uncertain so we don't drop good listings.
    """
    text = " ".join(
        str(card.get(k) or "")
        for k in ("title", "snippet", "area", "postcode", "price_raw")
    )
    area_name, outcode = parse_card_location(text)
    if not outcode:
        outcode = extract_outcode(card.get("postcode"))
    if not area_name and card.get("area"):
        area_name = str(card.get("area"))

    # Budget (use card price if parsed well)
    pcm = card.get("price_pcm")
    if pcm is None:
        pcm, _, _ = parse_card_price(text)
    if pcm is not None and pcm > max_pcm * 1.02:  # tiny slack for rounding
        return PrefilterResult(
            skip=True,
            reason=f"over budget £{pcm:.0f} > £{max_pcm:.0f}",
            outcode=outcode,
            stage="search",
        )

    if outcode_is_hard_far(outcode):
        return PrefilterResult(
            skip=True,
            reason=f"outcode {outcode} denylist",
            outcode=outcode,
            stage="search",
        )

    far_place = place_name_is_hard_far(text)
    if far_place and not outcode:
        # Only skip on place name when we don't have a contradictory near outcode.
        # (A far place WITH a far outcode already returned above.)
        return PrefilterResult(
            skip=True,
            reason=f"place '{far_place}' denylist",
            outcode=outcode,
            stage="search",
        )

    lat = card.get("lat")
    lon = card.get("lon")
    if (
        lat is not None
        and lon is not None
        and office_lat is not None
        and office_lon is not None
    ):
        return should_skip_tfl(
            lat=float(lat),
            lon=float(lon),
            postcode=outcode,
            office_lat=office_lat,
            office_lon=office_lon,
            max_minutes=max_minutes,
            hard_max_km=hard_max_km,
            estimate_slack=estimate_slack,
        )

    return PrefilterResult(skip=False, reason="ok", outcode=outcode, stage="search")


def should_skip_tfl(
    *,
    lat: float | None,
    lon: float | None,
    postcode: str | None,
    office_lat: float,
    office_lon: float,
    max_minutes: int = 30,
    hard_max_km: float = 22.0,
    estimate_slack: float = 1.35,
) -> PrefilterResult:
    """
    Multi-signal prefilter before calling TfL (after we have detail coords).
    """
    outcode = extract_outcode(postcode) if postcode and len(postcode) > 4 else None
    if postcode and not outcode:
        # may already be an outcode
        outcode = extract_outcode(postcode) or (
            postcode.upper().replace(" ", "")
            if re.match(r"^[A-Z]{1,2}\d", postcode.upper().replace(" ", ""))
            else None
        )
    area = extract_area(outcode)

    if outcode_is_hard_far(outcode):
        return PrefilterResult(
            skip=True,
            reason=f"outcode {outcode} denylist (outer/commuter)",
            outcode=outcode,
            stage="detail",
        )

    if lat is None or lon is None:
        return PrefilterResult(skip=False, outcode=outcode, stage="detail")

    km = haversine_km(lat, lon, office_lat, office_lon)
    sector = _bearing_sector(lat - office_lat, lon - office_lon)

    if km > hard_max_km:
        return PrefilterResult(
            skip=True,
            reason=f"crow-flies {km:.1f} km > {hard_max_km} km hard cap",
            distance_km=round(km, 2),
            outcode=outcode,
            stage="detail",
        )

    est = estimate_pt_minutes(km, area=area, sector=sector)
    ceiling = max_minutes * estimate_slack
    if est > ceiling:
        return PrefilterResult(
            skip=True,
            reason=(
                f"rough ~{est:.0f} min est > {ceiling:.0f} min "
                f"({km:.1f} km, sector {sector}, area {area or '?'})"
            ),
            distance_km=round(km, 2),
            est_minutes=round(est, 1),
            outcode=outcode,
            stage="detail",
        )

    return PrefilterResult(
        skip=False,
        reason="ok",
        distance_km=round(km, 2),
        est_minutes=round(est, 1),
        outcode=outcode,
        stage="detail",
    )
