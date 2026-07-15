"""Budget floor (budget.min_pcm) — must be unit-safe for weekly-priced rooms."""

from flatfinder.config import AppConfig
from flatfinder.models import FailReason, JourneyResult, Listing
from flatfinder.prices import parse_price
from flatfinder.rank import score_listing

_OK = JourneyResult(status="OK", duration_minutes=20, transfers=1)


def _score(listing: Listing, cfg: AppConfig | None = None):
    cfg = cfg or AppConfig()
    # Disable the living-room requirement so these assert only the budget floor.
    cfg.filter.require_living_room = False
    return score_listing(listing, _OK, cfg, distance_km=2.0, prefilter_too_far=False)


def test_default_floor_is_900():
    assert AppConfig().budget.min_pcm == 900


def test_at_floor_is_kept():
    item = _score(Listing(id="1", url="u", price_pcm=900, postcode="SE1 1AA"))
    assert item.filter_pass is True
    assert item.fail_reason == FailReason.OK


def test_below_floor_dropped_as_under_budget():
    item = _score(Listing(id="2", url="u", price_pcm=899, postcode="SE1 1AA"))
    assert item.filter_pass is False
    assert item.fail_reason == FailReason.UNDER_BUDGET


def test_weekly_price_is_judged_on_normalised_pcm_not_raw_pw():
    """The whole point of the request: a room advertised per-week must be judged
    on its converted monthly price, never the raw weekly number."""
    pcm, pw, _ = parse_price("£250 pw")
    assert pw == 250 and pcm > 1000  # 250/wk ≈ £1083/month
    listing = Listing(id="3", url="u", price_pcm=pcm, price_pw=pw, postcode="SE1 1AA")
    item = _score(listing)
    # Naively comparing the weekly 250 against a 900 floor would wrongly drop it.
    assert item.filter_pass is True
    assert item.fail_reason == FailReason.OK


def test_unknown_price_is_never_dropped():
    item = _score(Listing(id="4", url="u", price_pcm=None, postcode="SE1 1AA"))
    assert item.filter_pass is True
    assert item.fail_reason == FailReason.OK


def test_floor_of_zero_disables_the_check():
    cfg = AppConfig()
    cfg.budget.min_pcm = 0
    item = _score(Listing(id="5", url="u", price_pcm=1, postcode="SE1 1AA"), cfg)
    assert item.filter_pass is True  # a £1 junk price still passes when floor off


def test_ceiling_still_wins():
    item = _score(Listing(id="6", url="u", price_pcm=2000, postcode="SE1 1AA"))
    assert item.filter_pass is False
    assert item.fail_reason == FailReason.OVER_BUDGET
