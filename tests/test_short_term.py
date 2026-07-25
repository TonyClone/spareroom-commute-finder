"""Short-term-only sublet detection + filter — conservative and fail-open."""

from flatfinder.config import AppConfig
from flatfinder.models import FailReason, JourneyResult, Listing
from flatfinder.rank import score_listing
from flatfinder.scraper.parse import parse_listing_detail
from flatfinder.shortterm import is_short_term_only, parse_term_months

_OK = JourneyResult(status="OK", duration_minutes=20, transfers=1)


def _listing(**kw) -> Listing:
    base = dict(id="1", url="u", price_pcm=1200, postcode="SE1 1AA")
    base.update(kw)
    return Listing(**base)


# ---------------------------------------------------------------------------
# Term parsing
# ---------------------------------------------------------------------------


def test_parse_term_months():
    assert parse_term_months("3 months") == 3
    assert parse_term_months("12 Months") == 12
    assert round(parse_term_months("6 weeks"), 1) == 1.4
    assert parse_term_months("None") is None
    assert parse_term_months("") is None
    assert parse_term_months(None) is None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_explicit_short_max_term_is_dropped():
    drop, why = is_short_term_only(_listing(max_term="3 months"))
    assert drop and "3 months" in why


def test_long_max_term_is_kept():
    drop, _ = is_short_term_only(_listing(max_term="12 months"))
    assert not drop


def test_unknown_max_term_fails_open():
    drop, _ = is_short_term_only(_listing(max_term=""))
    assert not drop
    drop, _ = is_short_term_only(_listing(max_term="None"))
    assert not drop


def test_short_term_only_wording_in_description():
    drop, _ = is_short_term_only(
        _listing(description="Lovely room, short term only while owner travels.")
    )
    assert drop


def test_sublet_title_is_dropped():
    assert is_short_term_only(_listing(title="Sublet: double room Jan-Mar"))[0]
    assert is_short_term_only(_listing(title="Short term let in Camden"))[0]


def test_spareroom_standard_field_is_not_a_match():
    # SpareRoom detail pages routinely say "Short term let considered?" — that
    # is NOT "short term only" and must never drop a listing.
    drop, _ = is_short_term_only(
        _listing(description="Bills included? Yes. Short term let considered? No.")
    )
    assert not drop


def test_no_subletting_allowed_is_not_a_match():
    drop, _ = is_short_term_only(
        _listing(description="Great flat. No subletting allowed. Min term 6 months.")
    )
    assert not drop


def test_short_or_long_term_is_ambiguous_and_kept():
    drop, _ = is_short_term_only(_listing(title="Short term or long term let welcome"))
    assert not drop


# ---------------------------------------------------------------------------
# Filter integration
# ---------------------------------------------------------------------------


def test_filter_drops_short_term_when_on():
    cfg = AppConfig()
    assert cfg.filter.exclude_short_term is True  # new default
    item = score_listing(
        _listing(max_term="2 months"), _OK, cfg, distance_km=2.0, prefilter_too_far=False
    )
    assert item.filter_pass is False
    assert item.fail_reason == FailReason.SHORT_TERM


def test_filter_off_keeps_short_term():
    cfg = AppConfig()
    cfg.filter.exclude_short_term = False
    item = score_listing(
        _listing(max_term="2 months"), _OK, cfg, distance_km=2.0, prefilter_too_far=False
    )
    assert item.filter_pass is True


def test_threshold_is_configurable():
    cfg = AppConfig()
    cfg.filter.short_term_max_months = 6
    item = score_listing(
        _listing(max_term="5 months"), _OK, cfg, distance_km=2.0, prefilter_too_far=False
    )
    assert item.fail_reason == FailReason.SHORT_TERM


# ---------------------------------------------------------------------------
# Detail-page parsing feeds the fields
# ---------------------------------------------------------------------------

_PAGE = """
<html><body>
<h1>Double room in Bow</h1>
<dl class="feature-list">
  <dt class="feature-list__key">Minimum term</dt>
  <dd class="feature-list__value">1 month</dd>
  <dt class="feature-list__key">Maximum term</dt>
  <dd class="feature-list__value">3 months</dd>
</dl>
</body></html>
"""


def test_detail_parse_extracts_terms():
    listing = parse_listing_detail(_PAGE, "111", "u")
    assert listing.max_term == "3 months"
    assert listing.min_term == "1 month"
