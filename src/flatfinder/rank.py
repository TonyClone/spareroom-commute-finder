from __future__ import annotations

from datetime import date

from flatfinder.availability import move_in_fit, soft_rank_penalty
from flatfinder.config import AppConfig
from flatfinder.models import FailReason, JourneyResult, Listing, ScoredListing
from flatfinder.shortterm import is_short_term_only


def _ideal_date(config: AppConfig) -> date | None:
    raw = config.preferences.ideal_move_in
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _attach_move_prefs(item: ScoredListing, config: AppConfig) -> ScoredListing:
    fit, parsed, note = move_in_fit(
        item.listing.available_from,
        _ideal_date(config),
        late_grace_days=config.preferences.late_grace_days,
    )
    item.move_fit = fit.value
    item.move_note = note
    item.available_date = parsed.isoformat() if parsed else None
    # Soft ranking only — never flip filter_pass for move-in when soft_only
    item.rank_score = float(item.rank_score) + soft_rank_penalty(fit)
    return item


def score_listing(
    listing: Listing,
    journey: JourneyResult | None,
    config: AppConfig,
    *,
    distance_km: float | None = None,
    prefilter_too_far: bool = False,
) -> ScoredListing:
    # Budget ceiling
    if listing.price_pcm is not None and listing.price_pcm > config.budget.max_pcm:
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.OVER_BUDGET,
            rank_score=10_000,
        )
        return _attach_move_prefs(item, config)

    # Budget floor — compares the NORMALISED monthly price (price_pcm), which the
    # parser derives from weekly prices too, so per-week listings are unit-safe.
    # Only fires when a price actually parsed; an unknown price is never dropped.
    if (
        config.budget.min_pcm
        and listing.price_pcm is not None
        and listing.price_pcm < config.budget.min_pcm
    ):
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.UNDER_BUDGET,
            rank_score=10_000,
        )
        return _attach_move_prefs(item, config)

    # Rough crow-flies prefilter (no TfL called)
    if prefilter_too_far:
        km = distance_km if distance_km is not None else 999.0
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.TOO_FAR,
            rank_score=9_500 + km,
        )
        return _attach_move_prefs(item, config)

    # Location
    has_loc = (listing.lat is not None and listing.lon is not None) or bool(listing.postcode)
    if config.filter.require_location and not has_loc:
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.NO_LOCATION,
            rank_score=9_000,
        )
        return _attach_move_prefs(item, config)

    if config.filter.double_only and listing.room_type:
        if "double" not in listing.room_type.lower():
            item = ScoredListing(
                listing=listing,
                journey=journey,
                filter_pass=False,
                fail_reason=FailReason.OTHER,
                rank_score=8_500,
            )
            return _attach_move_prefs(item, config)

    if config.filter.bills_included_only and listing.bills_included is False:
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.OTHER,
            rank_score=8_400,
        )
        return _attach_move_prefs(item, config)

    # Short-term-only sublets — fail-open: only drops on an explicit max term at
    # or under the threshold, or unambiguous "short term only"/sublet wording.
    if config.filter.exclude_short_term:
        is_short, _why = is_short_term_only(
            listing, max_months=config.filter.short_term_max_months
        )
        if is_short:
            item = ScoredListing(
                listing=listing,
                journey=journey,
                filter_pass=False,
                fail_reason=FailReason.SHORT_TERM,
                rank_score=8_460,
            )
            return _attach_move_prefs(item, config)

    # Shared living room — fail-open: only drop when the detail field EXPLICITLY
    # said "no". Unknown ("") is always kept, so a markup change never hides rooms.
    if config.filter.require_living_room and listing.living_room == "no":
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.NO_LIVING_ROOM,
            rank_score=8_450,
        )
        return _attach_move_prefs(item, config)

    if journey is None:
        item = ScoredListing(
            listing=listing,
            journey=None,
            filter_pass=False,
            fail_reason=FailReason.NO_JOURNEY,
            rank_score=8_000,
        )
        return _attach_move_prefs(item, config)

    if journey.status == "RATE_LIMITED":
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.TFL_LIMIT,
            rank_score=7_800,
        )
        return _attach_move_prefs(item, config)

    if journey.status == "UNREACHABLE":
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.UNREACHABLE,
            rank_score=7_500,
        )
        return _attach_move_prefs(item, config)

    if journey.status != "OK" or journey.duration_minutes is None:
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.NO_JOURNEY,
            rank_score=7_000,
        )
        return _attach_move_prefs(item, config)

    if journey.duration_minutes > config.commute.max_minutes:
        item = ScoredListing(
            listing=listing,
            journey=journey,
            filter_pass=False,
            fail_reason=FailReason.OVER_COMMUTE,
            rank_score=float(journey.duration_minutes),
        )
        return _attach_move_prefs(item, config)

    # Lower is better: minutes primary, then transfers, then price, then soft move fit
    price = listing.price_pcm or 9999
    transfers = journey.transfers or 0
    rank = journey.duration_minutes * 1000 + transfers * 10 + price / 10000

    item = ScoredListing(
        listing=listing,
        journey=journey,
        filter_pass=True,
        fail_reason=FailReason.OK,
        rank_score=rank,
    )
    return _attach_move_prefs(item, config)


def _living_room_tier(item: ScoredListing) -> int:
    """0 = has a (shared) living room · 1 = unknown · 2 = explicitly none."""
    lr = (item.listing.living_room or "").lower()
    if lr == "no":
        return 2
    if lr == "":
        return 1
    return 0  # "shared" (or any other present value)


def _move_in_proximity(item: ScoredListing, ideal: date | None) -> tuple[int, int]:
    """Sort key for closeness to the ideal move-in date (smaller = closer = first).

    Returns ``(0, |days from ideal|)`` for listings with a parsed availability date,
    and ``(1, 0)`` for unknown/undated ones so they sort after all known distances.
    Neutral when no ideal date is configured.
    """
    if ideal is None or not item.available_date:
        return (1, 0)
    try:
        d = date.fromisoformat(item.available_date)
    except ValueError:
        return (1, 0)
    return (0, abs((d - ideal).days))


def order_tabs(items: list[ScoredListing], config: AppConfig) -> list[ScoredListing]:
    """Stably reorder listings for browser-tab opening (ordering only — never drops).

    Best matches end up first (leftmost tab). Grouping is applied in priority order:
    shared living room first (primary), then availability closest to the ideal
    move-in date (secondary). `sorted` is stable, so the commute-based order from
    `sort_scored` is preserved as the final tiebreaker within a group. Each signal
    is independently toggle-able via `daily.living_room_first` / `daily.move_in_first`.
    """
    ideal = _ideal_date(config)
    lr_on = config.daily.living_room_first
    mi_on = config.daily.move_in_first

    def key(s: ScoredListing) -> tuple[int, tuple[int, int]]:
        return (
            _living_room_tier(s) if lr_on else 0,
            _move_in_proximity(s, ideal) if mi_on else (0, 0),
        )

    return sorted(items, key=key)


def sort_scored(items: list[ScoredListing]) -> list[ScoredListing]:
    fit_order = {"flexible": 0, "good": 1, "ok": 2, "unknown": 3, "early_only": 4, "late": 5}
    return sorted(
        items,
        key=lambda s: (
            0 if s.filter_pass else 1,
            s.journey.duration_minutes if s.journey and s.journey.duration_minutes is not None else 9999,
            s.journey.transfers if s.journey and s.journey.transfers is not None else 99,
            fit_order.get(s.move_fit, 3),
            s.listing.price_pcm if s.listing.price_pcm is not None else 99999,
            s.rank_score,
        ),
    )
