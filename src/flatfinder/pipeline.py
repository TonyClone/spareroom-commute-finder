from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from flatfinder.commute.tfl import TflJourneyClient, format_tfl_date, format_tfl_time, journey_cache_key, resolve_journey_date
from flatfinder.commute.usage import TflUsageTracker
from flatfinder.config import AppConfig, EnvSettings, load_config, load_env
from flatfinder.db import Database
from flatfinder.geo.postcodes import PostcodeClient, normalise_postcode
from flatfinder.geo.prefilter import should_skip_tfl
from flatfinder.models import JourneyResult, Listing, ScoredListing
from flatfinder.rank import score_listing, sort_scored
from flatfinder.scraper.http import HttpClient
from flatfinder.scraper.spareroom import SpareRoomScraper

logger = logging.getLogger(__name__)
Progress = Callable[[str], None]


@dataclass
class _ListingPlan:
    listing: Listing
    over_budget: bool
    under_budget: bool
    too_far: bool
    has_loc: bool
    distance_km: float | None
    needs_tfl: bool
    cache_key: str | None


def _progress_print(msg: str) -> None:
    print(msg, flush=True)


def enrich_geocode(listing: Listing, geo: PostcodeClient) -> Listing:
    if listing.lat is not None and listing.lon is not None:
        if listing.geo_confidence == "unknown":
            listing.geo_confidence = "exact"
        return listing
    if listing.postcode:
        listing.postcode = normalise_postcode(listing.postcode)
        coords = geo.geocode(listing.postcode)
        if coords:
            listing.lat, listing.lon = coords
            if listing.geo_confidence == "unknown":
                listing.geo_confidence = "postcode"
    return listing


def listing_cache_key(listing: Listing, config: AppConfig, tfl_time: str, tfl_date: str) -> str:
    return journey_cache_key(
        listing.lat,
        listing.lon,
        listing.postcode,
        config.office.lat,
        config.office.lon,
        tfl_time,
        tfl_date,
    )


def fetch_journey(listing: Listing, config: AppConfig, tfl: TflJourneyClient) -> JourneyResult:
    """Pure TfL fetch (no DB) — safe to call from a worker thread."""
    office = config.office
    commute = config.commute
    return tfl.journey(
        from_lat=listing.lat,
        from_lon=listing.lon,
        from_postcode=listing.postcode if listing.lat is None else None,
        to_lat=office.lat,
        to_lon=office.lon,
        time_hhmm=commute.time,
        date_spec=commute.date,
        time_is=commute.time_is,
    )


def get_journey_for_listing(
    listing: Listing,
    config: AppConfig,
    tfl: TflJourneyClient,
    db: Database,
) -> JourneyResult:
    """Cached single-listing journey (kept for callers/tests outside the pipeline)."""
    commute = config.commute
    tfl_date = format_tfl_date(resolve_journey_date(commute.date))
    tfl_time = format_tfl_time(commute.time)
    cache_key = listing_cache_key(listing, config, tfl_time, tfl_date)
    cached = db.get_journey(cache_key)
    if cached and cached.status == "OK":
        cached.cache_key = cache_key
        return cached
    journey = fetch_journey(listing, config, tfl)
    journey.cache_key = cache_key
    db.save_journey(
        cache_key,
        journey,
        origin_postcode=listing.postcode,
        origin_lat=listing.lat,
        origin_lon=listing.lon,
        dest_lat=config.office.lat,
        dest_lon=config.office.lon,
        arrive_time=tfl_time,
        journey_date=tfl_date,
    )
    return journey


def run_pipeline(
    config: AppConfig | None = None,
    env: EnvSettings | None = None,
    progress: Progress | None = None,
    skip_scrape: bool = False,
    listings: list[Listing] | None = None,
) -> tuple[int, list[ScoredListing]]:
    """Run scrape → geocode → commute → filter. Returns (run_id, scored)."""
    config = config or load_config()
    env = env or load_env()
    log = progress or _progress_print

    db = Database(config.resolved_db_path())
    run_id = db.start_run(
        {
            "office": config.office.model_dump(),
            "budget": config.budget.model_dump(),
            "commute": config.commute.model_dump(),
            "search": config.search.model_dump(),
        }
    )
    log(f"Run #{run_id} started → office {config.office.postcode}")

    http = HttpClient(
        proxy_url=env.proxy_url or None,
        use_proxy=config.scraper.use_proxy,
        delay_seconds=config.scraper.delay_seconds,
        timeout=config.scraper.timeout_seconds,
        max_retries=config.scraper.max_retries,
    )
    geo = PostcodeClient()
    tfl = TflJourneyClient(app_key=env.tfl_app_key, app_id=env.tfl_app_id)

    try:
        if listings is None:
            if skip_scrape:
                raise ValueError("skip_scrape requires listings=")
            scraper = SpareRoomScraper(config, http)
            known = db.have_scanned_ids if config.search.incremental else None
            if config.search.incremental:
                log(
                    f"Incremental scan on ({db.scanned_count()} listings scanned before) "
                    "— will stop at previously-seen frontier"
                )
            else:
                log("Full scan (incremental disabled) — walking to max_listings")
            listings = scraper.scrape(progress=log, known=known, geo=geo)
            if scraper.last_scanned_ids:
                db.mark_scanned(scraper.last_scanned_ids)
        log(f"Listings collected: {len(listings)}")

        workers = max(1, config.scraper.api_concurrency)
        # Keyless TfL has a much lower rate limit — throttle to avoid 429 storms.
        tfl_workers = (
            workers
            if env.tfl_app_key
            else max(1, min(workers, config.scraper.keyless_tfl_concurrency))
        )
        tfl_date = format_tfl_date(resolve_journey_date(config.commute.date))
        tfl_time = format_tfl_time(config.commute.time)

        # Phase 1 — geocode in parallel (postcodes.io; in-memory cache, no delay).
        if listings:
            log(f"Geocoding {len(listings)} listings (parallel ×{workers})…")
            with ThreadPoolExecutor(max_workers=workers) as ex:
                listings = list(ex.map(lambda l: enrich_geocode(l, geo), listings))
        for listing in listings:
            db.upsert_listing(listing)

        # Phase 2 — classify each listing + compute prefilter (pure, no I/O).
        plans: list[_ListingPlan] = []
        for listing in listings:
            has_coords = listing.lat is not None and listing.lon is not None
            has_loc = has_coords or bool(listing.postcode)
            over_budget = (
                listing.price_pcm is not None and listing.price_pcm > config.budget.max_pcm
            )
            under_budget = (
                bool(config.budget.min_pcm)
                and listing.price_pcm is not None
                and listing.price_pcm < config.budget.min_pcm
            )
            distance_km: float | None = None
            too_far = False
            if not over_budget and not under_budget and has_loc:
                pref = should_skip_tfl(
                    lat=listing.lat,
                    lon=listing.lon,
                    postcode=listing.postcode,
                    office_lat=config.office.lat,
                    office_lon=config.office.lon,
                    max_minutes=config.commute.max_minutes,
                    hard_max_km=config.commute.prefilter_max_km,
                    estimate_slack=config.commute.prefilter_estimate_slack,
                )
                too_far = pref.skip
                distance_km = pref.distance_km
            needs_tfl = (not over_budget) and (not under_budget) and (not too_far) and has_loc
            cache_key = (
                listing_cache_key(listing, config, tfl_time, tfl_date) if needs_tfl else None
            )
            plans.append(
                _ListingPlan(
                    listing, over_budget, under_budget, too_far, has_loc, distance_km, needs_tfl, cache_key
                )
            )

        skipped_far = sum(1 for p in plans if p.too_far)

        # Phase 3 — resolve journeys. Read cache first (main thread), dedup misses
        # by cache_key, then fetch the unique misses in parallel (TfL only, no DB).
        journeys: dict[str, JourneyResult] = {}
        miss_rep: dict[str, Listing] = {}
        for p in plans:
            if not p.needs_tfl or not p.cache_key:
                continue
            if p.cache_key in journeys or p.cache_key in miss_rep:
                continue
            cached = db.get_journey(p.cache_key)
            if cached and cached.status == "OK":
                cached.cache_key = p.cache_key
                journeys[p.cache_key] = cached
            else:
                miss_rep[p.cache_key] = p.listing
        tfl_cached = len(journeys)  # unique journeys served from cache

        # Optional pre-emptive daily cap. Off by default: fetch everything and let
        # real 429s (below) surface as INCOMPLETE — no self-throttling. Only when a
        # daily limit is configured do we stop early and leave the rest unevaluated.
        miss_items = list(miss_rep.items())
        limit_cfg = config.commute.tfl_daily_limit
        tfl_usage: TflUsageTracker | None = None
        if limit_cfg:
            tfl_usage = TflUsageTracker(
                path=db.path.parent / "tfl_usage.json", daily_limit=limit_cfg
            )
            remaining = tfl_usage.remaining()
            to_fetch = miss_items[:remaining]
            over_limit = miss_items[remaining:]
        else:
            to_fetch = miss_items
            over_limit = []

        if miss_items:
            quota = f"{tfl_usage.summary()} · " if tfl_usage else ""
            log(
                f"TfL: {quota}{tfl_cached} cached · {len(to_fetch)} to fetch"
                + (f" · ⚠ {len(over_limit)} OVER DAILY LIMIT (skipped)" if over_limit else "")
            )
        for key, _rep in over_limit:
            journeys[key] = JourneyResult(
                status="RATE_LIMITED",
                error="TfL daily limit reached — not evaluated this run",
                cache_key=key,
            )

        rate_limited_hits = 0
        if to_fetch:
            keyless_note = "" if env.tfl_app_key else " keyless — throttled"
            log(f"TfL: fetching {len(to_fetch)} journeys (parallel ×{tfl_workers}{keyless_note})…")

            def _fetch(item: tuple[str, Listing]) -> tuple[str, JourneyResult]:
                key, rep = item
                journey = fetch_journey(rep, config, tfl)
                journey.cache_key = key
                return key, journey

            with ThreadPoolExecutor(max_workers=tfl_workers) as ex:
                for key, journey in ex.map(_fetch, to_fetch):
                    journeys[key] = journey
                    if journey.status == "RATE_LIMITED":
                        rate_limited_hits += 1
                    elif journey.status == "OK":
                        rep = miss_rep[key]
                        db.save_journey(  # main thread — single writer, OK only
                            key,
                            journey,
                            origin_postcode=rep.postcode,
                            origin_lat=rep.lat,
                            origin_lon=rep.lon,
                            dest_lat=config.office.lat,
                            dest_lon=config.office.lon,
                            arrive_time=tfl_time,
                            journey_date=tfl_date,
                        )
            if tfl_usage is not None:
                tfl_usage.record(len(to_fetch))  # count attempts against the quota

        tfl_fetched = len(to_fetch)
        tfl_incomplete = len(over_limit) + rate_limited_hits

        # Phase 4 — score everything.
        scored: list[ScoredListing] = []
        for p in plans:
            journey = journeys.get(p.cache_key) if p.needs_tfl and p.cache_key else None
            scored.append(
                score_listing(
                    p.listing,
                    journey,
                    config,
                    distance_km=p.distance_km,
                    prefilter_too_far=p.too_far,
                )
            )

        scored = sort_scored(scored)
        db.save_scores(run_id, scored)
        passed = [s for s in scored if s.filter_pass]
        db.finish_run(run_id, listing_count=len(scored), pass_count=len(passed))
        if tfl_incomplete:
            cause = (
                f"TfL daily limit reached ({tfl_usage.summary()})"
                if tfl_usage is not None and over_limit
                else f"TfL rate-limited {rate_limited_hits} call(s) (HTTP 429)"
            )
            log(
                f"⚠️  INCOMPLETE RUN — {tfl_incomplete} listing(s) NOT evaluated: {cause}. "
                f"These are marked TFL_LIMIT (not cached) and will be retried on the "
                f"next run. Results below are PARTIAL."
            )
        log(
            f"Done. {len(passed)}/{len(scored)} pass ≤{config.commute.max_minutes} min "
            f"& £{config.budget.max_pcm} · TfL fetched {tfl_fetched} (+{tfl_cached} cached) · "
            f"prefilter skipped {skipped_far} (>{config.commute.prefilter_max_km} km)"
            + (f" · ⚠ {tfl_incomplete} NOT evaluated (TfL limit)" if tfl_incomplete else "")
        )
        return run_id, scored
    finally:
        http.close()
        geo.close()
        tfl.close()
