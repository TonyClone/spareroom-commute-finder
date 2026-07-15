from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.parse import quote_plus, urlencode

from flatfinder.config import AppConfig
from flatfinder.geo.postcodes import PostcodeClient
from flatfinder.geo.prefilter import extract_outcode, should_skip_search_card, should_skip_tfl
from flatfinder.models import Listing
from flatfinder.prices import pcm_to_pw
from flatfinder.scraper.http import HttpClient
from flatfinder.scraper.parse import (
    extract_search_id,
    next_page_href,
    parse_listing_detail,
    parse_search_results,
)

logger = logging.getLogger(__name__)

BASE = "https://www.spareroom.co.uk"


def build_search_url(config: AppConfig, offset: int = 0) -> str:
    if config.search.search_url:
        url = config.search.search_url
        if "offset=" in url:
            return re.sub(r"offset=\d+", f"offset={offset}", url)
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}offset={offset}"

    max_pw = int(config.budget.max_pw or pcm_to_pw(config.budget.max_pcm))
    params = {
        "search_id": "",
        "mode": "list",
        "show_results": "1",
        "city_id": "",
        "flatshare_type": "offered",
        "location_type": "area",
        "search": config.search.location,
        "miles_from_max": str(config.resolved_radius_miles()),
        "max_rent": str(max_pw),
        "per": "pw",
        "min_rent": "0",
        "rooms_for": "",
        "action": "search",
        "max_per_page": "20",
        "offset": str(offset),
        "sort_by": "age",
        "nmsq_mode": "normal",
        "available_search": "N",
        "min_term": "0",
        "max_term": "0",
        "days_of_wk_available": "7 days a week",
        "showme_rooms": "Y",
        "showme_1beds": "Y",
        "showme_buddyup_properties": "Y",
    }
    return f"{BASE}/flatshare/?{urlencode(params)}"


def build_search_url_alt(config: AppConfig, offset: int = 0) -> str:
    """Alternate search.pl URL shape."""
    max_pw = int(config.budget.max_pw or pcm_to_pw(config.budget.max_pcm))
    q = quote_plus(config.search.location)
    miles = config.resolved_radius_miles()
    return (
        f"{BASE}/flatshare/search.pl?nmsq_mode=normal&action=search"
        f"&max_per_page=20&flatshare_type=offered&search={q}"
        f"&miles_from_max={miles}&mode=list"
        f"&min_rent=0&max_rent={max_pw}&per=pw"
        f"&available_search=N&min_term=0&max_term=0"
        f"&days_of_wk_available=7+days+a+week&showme_rooms=Y"
        f"&showme_1beds=Y&offset={offset}"
    )


class SpareRoomScraper:
    def __init__(self, config: AppConfig, http: HttpClient):
        self.config = config
        self.http = http
        self.last_stats: dict[str, int] = {
            "search_found": 0,
            "early_skipped": 0,
            "prescreen_skipped": 0,
            "details_fetched": 0,
            "detail_failed": 0,
        }
        self._last_url: str = BASE
        self.last_scanned_ids: list[str] = []

    def fetch_search_page(self, offset: int = 0) -> str:
        urls = []
        if self.config.search.search_url:
            urls.append(build_search_url(self.config, offset))
        else:
            urls.append(build_search_url_alt(self.config, offset))
            urls.append(build_search_url(self.config, offset))

        last_html = ""
        for url in urls:
            logger.info("Fetching search offset=%s url=%s", offset, url[:120])
            try:
                r = self.http.get(url)
                last_html = r.text
                self._last_url = str(r.url)
                results = parse_search_results(last_html)
                if results:
                    return last_html
            except Exception as e:
                logger.warning("Search fetch failed for %s: %s", url[:80], e)
        return last_html

    def _page_url(self, search_id: str, offset: int, page_size: int = 20) -> str:
        """Canonical list-mode results URL for a page of an established search.

        Pagination is gated on search_id; mode=list keeps us off the map view
        (whose pages carry no listing anchors). sort_by=age = newest first, so
        the incremental watermark can stop at previously-scraped listings.
        """
        return (
            f"{BASE}/flatshare/?search_id={search_id}&mode=list"
            f"&max_per_page={page_size}&sort_by=age&offset={offset}"
        )

    def iter_search_listings(
        self,
        progress: Callable[[str], None] | None = None,
        known: Callable[[list[str]], set[str]] | None = None,
    ) -> list[dict]:
        """Walk all result pages of the search.

        The first request establishes the search — SpareRoom assigns a search_id
        and 302s to /flatshare/?search_id=... . Every later page is fetched by
        synthesising a list-mode URL that carries that search_id. Blindly
        stepping offset on search.pl (the old approach) re-runs the search and
        returns page 1 forever — the bug this replaces.

        ``known(ids) -> already-scanned subset`` enables incremental runs: since
        results are newest-first, the first page that adds no previously-unseen
        id means we've reached prior-run territory, and we stop.
        """
        max_pages = self.config.search.max_pages
        max_listings = self.config.search.max_listings
        page_size = 20
        all_items: dict[str, dict] = {}
        scanned_ids: list[str] = []

        first = self.fetch_search_page(0)
        search_id = extract_search_id(first)
        # Re-fetch page 1 through the canonical list-mode URL so every page
        # (0..N) shares the same view + sort, keeping pagination consistent.
        if search_id:
            try:
                r = self.http.get(self._page_url(search_id, 0, page_size))
                html = r.text
                self._last_url = str(r.url)
            except Exception:
                html = first
        else:
            html = first
            logger.warning("No search_id from initial search — pagination may not advance")
        current_offset = 0
        page = 0

        while True:
            batch = parse_search_results(html)
            page_ids = [item["id"] for item in batch]
            new = 0
            for item in batch:
                if item["id"] not in all_items:
                    all_items[item["id"]] = item
                    new += 1

            # Incremental watermark: how many ids on this page are new to us
            # across all prior runs (not just this walk)?
            fresh_to_history = len(page_ids)
            if known is not None and page_ids:
                already = known(page_ids)
                fresh_to_history = len(set(page_ids) - already)
            scanned_ids.extend(page_ids)

            page += 1
            if progress:
                extra = "" if known is None else f", new_since_last={fresh_to_history}"
                progress(
                    f"Search page {page}: found {len(batch)} listings "
                    f"(offset={current_offset}, unique so far={len(all_items)}, "
                    f"search_id={search_id or '—'}{extra})"
                )
            if not batch or new == 0:
                # Empty page or a page that adds nothing new (looped) → done.
                break
            if known is not None and fresh_to_history == 0:
                # Whole page already scanned in a prior run → caught up.
                if progress:
                    progress(f"Incremental stop: page {page} fully already-scanned")
                break
            if page >= max_pages or len(all_items) >= max_listings:
                break

            next_offset = current_offset + page_size
            if search_id:
                nxt = self._page_url(search_id, next_offset, page_size)
            else:
                # No search_id (rare) — fall back to following the page's own
                # next link, resolved against the last fetched URL.
                nxt = next_page_href(html, current_offset, base_url=self._last_url)
                if not nxt:
                    logger.warning("No search_id and no next link — stopping at page %s", page)
                    break
                m = re.search(r"offset=(\d+)", nxt)
                next_offset = int(m.group(1)) if m else next_offset

            try:
                r = self.http.get(nxt)
                html = r.text
                self._last_url = str(r.url)
            except Exception as e:
                logger.warning("Next-page fetch failed at offset=%s: %s", next_offset, e)
                break
            current_offset = next_offset
            if not search_id:
                search_id = extract_search_id(html)

        self.last_scanned_ids = scanned_ids
        return list(all_items.values())[:max_listings]

    def early_filter(
        self,
        cards: list[dict],
        progress: Callable[[str], None] | None = None,
    ) -> list[dict]:
        """Drop hopeless cards before any detail HTTP request."""
        kept: list[dict] = []
        skipped = 0
        for card in cards:
            result = should_skip_search_card(
                card,
                max_pcm=self.config.budget.max_pcm,
                max_minutes=self.config.commute.max_minutes,
                hard_max_km=self.config.commute.prefilter_max_km,
                estimate_slack=self.config.commute.prefilter_estimate_slack,
                office_lat=self.config.office.lat,
                office_lon=self.config.office.lon,
            )
            if result.skip:
                skipped += 1
                logger.info(
                    "Early skip %s: %s [%s]",
                    card.get("id"),
                    result.reason,
                    (card.get("area") or card.get("postcode") or "")[:40],
                )
                if progress and skipped <= 8:
                    progress(f"Early skip: {result.reason} ({card.get('id')})")
                continue
            kept.append(card)
        self.last_stats["early_skipped"] = skipped
        if progress:
            progress(
                f"Early prefilter: kept {len(kept)}/{len(cards)} "
                f"(skipped {skipped} before detail scrape)"
            )
        return kept

    def prescreen_by_outcode(
        self,
        cards: list[dict],
        geo: PostcodeClient,
        progress: Callable[[str], None] | None = None,
    ) -> list[dict]:
        """Drop clearly-unreachable cards by their outcode centroid *before* the
        (polite, slow) detail fetch — fewer SpareRoom hits, politer and faster.

        Reuses should_skip_tfl with the same slack as the detail stage, so a kept
        listing loses nothing: exact coords are re-checked after the detail fetch.
        Fail-open when an outcode can't be resolved.
        """
        office = self.config.office
        commute = self.config.commute
        workers = max(1, self.config.scraper.api_concurrency)

        for c in cards:
            c["_outcode"] = c.get("outcode") or extract_outcode(c.get("postcode"))
        keys = sorted({c["_outcode"] for c in cards if c["_outcode"]})
        coords_by_outcode: dict[str, tuple[float, float] | None] = {}
        if keys:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                coords_by_outcode = dict(zip(keys, ex.map(geo.geocode, keys)))

        kept: list[dict] = []
        skipped = 0
        for c in cards:
            coords = coords_by_outcode.get(c["_outcode"]) if c["_outcode"] else None
            if not coords:
                kept.append(c)  # unknown location → fetch detail to learn more
                continue
            res = should_skip_tfl(
                lat=coords[0],
                lon=coords[1],
                postcode=c["_outcode"],
                office_lat=office.lat,
                office_lon=office.lon,
                max_minutes=commute.max_minutes,
                hard_max_km=commute.prefilter_max_km,
                estimate_slack=commute.prefilter_estimate_slack,
            )
            if res.skip:
                skipped += 1
                logger.info("Pre-screen skip %s: %s [%s]", c.get("id"), res.reason, c["_outcode"])
            else:
                kept.append(c)
        self.last_stats["prescreen_skipped"] = skipped
        if progress:
            progress(
                f"Outcode pre-screen: kept {len(kept)}/{len(cards)} "
                f"(skipped {skipped} unreachable before detail fetch)"
            )
        return kept

    def fetch_detail(self, listing_id: str, url: str) -> Listing:
        r = self.http.get(url)
        return parse_listing_detail(r.text, listing_id, url)

    def scrape(
        self,
        progress: Callable[[str], None] | None = None,
        known: Callable[[list[str]], set[str]] | None = None,
        geo: PostcodeClient | None = None,
    ) -> list[Listing]:
        summaries = self.iter_search_listings(progress=progress, known=known)
        self.last_stats["search_found"] = len(summaries)
        summaries = self.early_filter(summaries, progress=progress)
        if geo is not None:
            summaries = self.prescreen_by_outcode(summaries, geo, progress=progress)

        # Safety valve for long gaps (e.g. monthly use): detail fetches are polite
        # and sequential, so cap how many we do per run. summaries are newest-first,
        # so we keep the freshest listings — the ones most likely still available.
        cap = self.config.search.max_details_per_run
        self.last_stats["detail_capped"] = 0
        if cap and len(summaries) > cap:
            self.last_stats["detail_capped"] = len(summaries) - cap
            if progress:
                progress(
                    f"Large batch: fetching details for the newest {cap} of "
                    f"{len(summaries)} candidates this run (raise "
                    f"search.max_details_per_run to process more)."
                )
            summaries = summaries[:cap]

        listings: list[Listing] = []
        for i, item in enumerate(summaries, 1):
            if progress:
                progress(f"Detail {i}/{len(summaries)}: {item['id']}")
            try:
                listing = self.fetch_detail(item["id"], item["url"])
                self.last_stats["details_fetched"] += 1
                if listing.price_pcm is None and item.get("price_pcm") is not None:
                    listing.price_pcm = item["price_pcm"]
                    listing.price_pw = item.get("price_pw")
                    listing.price_raw = item.get("price_raw") or listing.price_raw
                if not listing.postcode and item.get("postcode"):
                    listing.postcode = item["postcode"]
                    if listing.geo_confidence == "unknown":
                        listing.geo_confidence = "postcode"
                if not listing.area and item.get("area"):
                    listing.area = item["area"]
                if not listing.title or listing.title.startswith("Listing "):
                    listing.title = item.get("title") or listing.title
                listings.append(listing)
            except Exception as e:
                self.last_stats["detail_failed"] += 1
                logger.warning("Failed detail %s: %s", item["id"], e)
                listings.append(
                    Listing(
                        id=item["id"],
                        url=item["url"],
                        title=item.get("title") or f"Listing {item['id']}",
                        price_pcm=item.get("price_pcm"),
                        price_pw=item.get("price_pw"),
                        price_raw=item.get("price_raw") or "",
                        postcode=item.get("postcode"),
                        area=item.get("area") or "",
                        geo_confidence="postcode" if item.get("postcode") else "unknown",
                        raw={"error": str(e)},
                    )
                )
        if progress:
            s = self.last_stats
            progress(
                f"Scrape done: search={s['search_found']} early_skip={s['early_skipped']} "
                f"prescreen_skip={s['prescreen_skipped']} "
                f"details={s['details_fetched']} fail={s['detail_failed']}"
            )
        return listings
