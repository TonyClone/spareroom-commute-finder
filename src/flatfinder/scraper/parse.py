from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from flatfinder.geo.postcodes import extract_postcode, normalise_postcode
from flatfinder.geo.prefilter import parse_card_location, parse_card_price
from flatfinder.models import Listing
from flatfinder.prices import parse_price

BASE = "https://www.spareroom.co.uk"

LISTING_HREF_RE = re.compile(r"/flatshare/flatshare_detail\.pl\?.*?flatshare_id=(\d+)", re.I)
ID_IN_URL_RE = re.compile(r"flatshare_id=(\d+)", re.I)
LATLON_RE = re.compile(
    r'["\']?(?:latitude|lat)["\']?\s*[:=]\s*["\']?(-?\d+\.\d+)["\']?.*?'
    r'["\']?(?:longitude|lng|lon)["\']?\s*[:=]\s*["\']?(-?\d+\.\d+)',
    re.I | re.S,
)
LATLON_RE2 = re.compile(
    r'["\']?(?:longitude|lng|lon)["\']?\s*[:=]\s*["\']?(-?\d+\.\d+)["\']?.*?'
    r'["\']?(?:latitude|lat)["\']?\s*[:=]\s*["\']?(-?\d+\.\d+)',
    re.I | re.S,
)
COORDS_PAIR_RE = re.compile(r"(-?\d{1,2}\.\d{3,}),\s*(-?\d{1,2}\.\d{3,})")


def absolute_url(href: str) -> str:
    return urljoin(BASE, href)


def parse_search_results(html: str) -> list[dict[str, Any]]:
    """Return minimal listing dicts from a search results page."""
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, dict[str, Any]] = {}

    # Primary: anchors to flatshare_detail
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = ID_IN_URL_RE.search(href)
        if not m:
            continue
        lid = m.group(1)
        if lid in found:
            continue
        # Normalise detail URL (drop tracking junk / broken ? fragments)
        clean_href = href.split("#")[0]
        id_m = ID_IN_URL_RE.search(clean_href)
        url = absolute_url(
            f"/flatshare/flatshare_detail.pl?flatshare_id={id_m.group(1)}"
            if id_m
            else clean_href
        )
        title = a.get_text(" ", strip=True)
        # Prefer short title from early text (before long marketing blurbs)
        if len(title) > 80:
            title = title[:80].rsplit(" ", 1)[0]
        # Climb for a reasonably small card container (avoid body/html)
        card = a
        best = a
        for _ in range(8):
            parent = card.parent
            if parent is None or parent.name in {"body", "html", "[document]"}:
                break
            card = parent
            classes = " ".join(card.get("class") or []).lower()
            if any(k in classes for k in ("listing", "result", "panel", "card", "advert")):
                best = card
                break
            # Prefer the smallest ancestor that already contains a price
            text_try = card.get_text(" ", strip=True)
            if "£" in text_try and len(text_try) < 800:
                best = card
        text = best.get_text(" ", strip=True) if best else title
        # Prefer rent from full card (snippet has "£750 pcm"); avoid "£9 photos"
        price_pcm, price_pw, price_raw = parse_card_price(text)
        if price_pcm is None:
            price_pcm, price_pw, price_raw = parse_card_price(title)
        area_name, outcode = parse_card_location(text)
        pc = extract_postcode(text) or extract_postcode(title)
        # Synthetic postcode for prefilter: full if present else outcode
        loc_pc = pc or outcode
        found[lid] = {
            "id": lid,
            "url": url,
            "title": title[:200] if title else f"Listing {lid}",
            "price_pcm": price_pcm,
            "price_pw": price_pw,
            "price_raw": price_raw,
            "postcode": loc_pc,
            "outcode": outcode,
            "area": area_name or "",
            "snippet": text[:500],
        }

    # JSON-LD / embedded scripts sometimes list results
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            m = ID_IN_URL_RE.search(url)
            if not m:
                continue
            lid = m.group(1)
            if lid in found:
                continue
            found[lid] = {
                "id": lid,
                "url": absolute_url(url),
                "title": str(item.get("name") or f"Listing {lid}")[:200],
                "price_pcm": None,
                "price_pw": None,
                "price_raw": "",
                "postcode": extract_postcode(str(item.get("address") or "")),
                "area": "",
                "snippet": "",
            }

    return list(found.values())


def _text_after_label(soup: BeautifulSoup, labels: list[str]) -> str:
    page_text = soup.get_text("\n", strip=True)
    for label in labels:
        # Label on its own line, value on next
        m = re.search(
            rf"{re.escape(label)}\s*[:\n]\s*([^\n]+)",
            page_text,
            re.I,
        )
        if m:
            return m.group(1).strip()
    return ""


def _feature_list_value(soup: BeautifulSoup, keys: list[str]) -> tuple[str | None, str]:
    """Read a value from SpareRoom's structured `<dl class="feature-list">` block.

    Detail pages render key facts as `<dt class="feature-list__key">Living room</dt>
    <dd class="feature-list__value"><span class="tick">shared</span></dd>` pairs.
    This is far more reliable than scraping labels out of flowed page text.

    Returns ``(value_text, span_class)`` for the first matching key, or
    ``(None, "")`` if the key isn't present. ``span_class`` carries SpareRoom's
    ``tick``/``cross`` marker so callers can read a boolean even if the visible
    text varies. Key matching is case-insensitive and ignores a trailing "?".
    """
    wanted = {k.lower().rstrip("?").strip() for k in keys}
    for dt in soup.select("dt.feature-list__key"):
        key = dt.get_text(" ", strip=True).lower().rstrip("?").strip()
        if key in wanted:
            dd = dt.find_next_sibling("dd")
            if dd is None:
                return None, ""
            span = dd.select_one("span")
            span_class = " ".join(span.get("class") or []) if span else ""
            return dd.get_text(" ", strip=True), span_class
    return None, ""


def parse_living_room(soup: BeautifulSoup) -> str:
    """Tri-state read of the shared "Living room" fact from the detail page.

    Returns ``"no"`` when the flat explicitly has no living room, the SpareRoom
    value (typically ``"shared"``) when it has one, or ``""`` when the field is
    absent/blank. Deliberately fail-open: anything we can't read stays ``""``
    (unknown), never ``"no"``, so the require-living-room filter can't hide a
    room just because the markup changed.
    """
    text, span_class = _feature_list_value(soup, ["Living room"])
    if text is None:
        return ""  # field not on the page → unknown
    value = text.strip().lower()
    if not value and "tick" not in span_class and "cross" not in span_class:
        return ""  # blank and unmarked → unknown
    if "cross" in span_class or value in {"no", "none"}:
        return "no"
    return value or "shared"  # tick / "shared" / "own" / "yes" → present


def _find_lat_lon(html: str) -> tuple[float | None, float | None]:
    m = LATLON_RE.search(html)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = LATLON_RE2.search(html)
    if m:
        # lon, lat order
        return float(m.group(2)), float(m.group(1))
    # spareroom sometimes uses centre: [lon, lat] or lat/lng pairs near "map"
    for m in COORDS_PAIR_RE.finditer(html):
        a, b = float(m.group(1)), float(m.group(2))
        # UK lat ~49-59, lon ~-8 to 2
        if 49 <= a <= 59 and -8 <= b <= 2:
            return a, b
        if 49 <= b <= 59 and -8 <= a <= 2:
            return b, a
    return None, None


def parse_listing_detail(html: str, listing_id: str, url: str) -> Listing:
    soup = BeautifulSoup(html, "lxml")

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)

    page_text = soup.get_text(" ", strip=True)
    price_pcm, price_pw, price_raw = parse_price(page_text)

    # Prefer feature feature list prices
    for sel in (".feature-list", "#features", ".room-list", ".key-features"):
        block = soup.select_one(sel)
        if block:
            p2, w2, r2 = parse_price(block.get_text(" ", strip=True))
            if p2 is not None:
                price_pcm, price_pw, price_raw = p2, w2, r2
                break

    postcode = extract_postcode(page_text)
    # Stronger: look near "Postcode" label
    labeled_pc = _text_after_label(soup, ["Postcode", "Postal code"])
    if labeled_pc:
        postcode = normalise_postcode(extract_postcode(labeled_pc) or labeled_pc) or postcode

    lat, lon = _find_lat_lon(html)
    geo_confidence = "unknown"
    if lat is not None and lon is not None:
        geo_confidence = "exact"
    elif postcode:
        geo_confidence = "postcode"

    area = _text_after_label(soup, ["Area", "Location", "Neighbourhood"])
    if not area:
        # Often in title: "Double room in Shoreditch"
        m = re.search(r"\bin\s+([A-Za-z][A-Za-z\s\-']{2,40})$", title)
        if m:
            area = m.group(1).strip()

    room_type = _text_after_label(soup, ["Type of room", "Room type", "Type"])
    living_room = parse_living_room(soup)
    available = _text_after_label(soup, ["Available", "Available from"])
    nearest = _text_after_label(soup, ["Nearest station", "Station"])
    bills_text = _text_after_label(soup, ["Bills included", "Bills"])
    bills_included: bool | None = None
    if bills_text:
        low = bills_text.lower()
        if "yes" in low or "included" in low:
            bills_included = True
        elif "no" in low or "not" in low:
            bills_included = False

    # Description
    description = ""
    for sel in ("#description", ".description", "#listing_description", "article"):
        el = soup.select_one(sel)
        if el:
            description = el.get_text(" ", strip=True)[:1500]
            break
    if not description:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            description = str(meta["content"])[:1500]

    image_url = ""
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image_url = str(og["content"])

    return Listing(
        id=listing_id,
        url=url,
        title=title or f"Listing {listing_id}",
        price_raw=price_raw,
        price_pcm=price_pcm,
        price_pw=price_pw,
        postcode=postcode,
        lat=lat,
        lon=lon,
        geo_confidence=geo_confidence,
        area=area,
        room_type=room_type,
        living_room=living_room,
        bills_included=bills_included,
        available_from=available,
        nearest_station=nearest,
        description=description,
        image_url=image_url,
        raw={"source": "spareroom_detail"},
    )


_SEARCH_ID_RE = re.compile(r"search_id=(\d+)", re.I)
# Text/markup hints that an anchor is the "next page" control.
_NEXT_HINTS = ("next", "»", "&raquo;", "›", "&rsaquo;", ">>")


def extract_search_id(html: str) -> str | None:
    """SpareRoom assigns a search_id per search; pagination requires it.

    Without it, `search.pl?...&offset=N` re-runs the search and returns page 1,
    which is why blind offset-stepping never advances. We capture the id here so
    callers can follow real pagination links that carry it.
    """
    for m in _SEARCH_ID_RE.finditer(html):
        sid = m.group(1)
        if sid and sid != "0":
            return sid
    return None


def _is_map_href(href: str) -> bool:
    low = href.lower()
    return "as+a+map" in low or "as a map" in low or "show_results=as" in low or "mode=map" in low


def next_page_href(
    html: str, current_offset: int, base_url: str = BASE
) -> str | None:
    """Absolute URL of the next *list-mode* results page, or None if last page.

    Fallback for when no search_id is available (the scraper normally synthesises
    the next URL from search_id). SpareRoom's pagination anchors are query-only
    relatives (`?offset=20&...`), so they must be resolved against the current
    page URL, not the bare host — pass the page's final URL as base_url. Map-view
    links are skipped because those pages carry no list anchors.
    """
    soup = BeautifulSoup(html, "lxml")
    best_next: str | None = None
    nearest_href: str | None = None
    nearest_off: int | None = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if _is_map_href(href):
            continue
        m = re.search(r"offset=(\d+)", href)
        if not m:
            continue
        off = int(m.group(1))
        if off <= current_offset:
            continue
        text = a.get_text(" ", strip=True).lower()
        rel = " ".join(a.get("rel") or []).lower()
        cls = " ".join(a.get("class") or []).lower()
        if best_next is None and (
            any(h in text for h in _NEXT_HINTS) or "next" in rel or "next" in cls
        ):
            best_next = href
        if nearest_off is None or off < nearest_off:
            nearest_off = off
            nearest_href = href
    chosen = best_next or nearest_href
    return urljoin(base_url, chosen) if chosen else None
