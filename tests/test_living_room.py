"""Shared living-room detection + filter.

Parsing reads SpareRoom's structured feature-list, and the filter is fail-open:
it only drops a room when the field explicitly says "No".
"""

from flatfinder.config import AppConfig
from flatfinder.models import FailReason, JourneyResult, Listing, ScoredListing
from flatfinder.rank import order_tabs, score_listing
from flatfinder.scraper.parse import parse_listing_detail, parse_living_room

from bs4 import BeautifulSoup

# Minimal page shaped like a real SpareRoom detail feature-list.
_PAGE = """
<html><body>
<h1>Double room {where}</h1>
<dl class="feature-list">
  <dt class="feature-list__key">Bills included?</dt>
  <dd class="feature-list__value">Yes</dd>
  <dt class="feature-list__key">Living room</dt>
  <dd class="feature-list__value"><span class="{cls}">{val}</span></dd>
  <dt class="feature-list__key">Wifi</dt>
  <dd class="feature-list__value"><span class="tick">Yes</span></dd>
</dl>
</body></html>
"""

_OK = JourneyResult(status="OK", duration_minutes=20, transfers=1)


def _lr(html: str) -> str:
    return parse_living_room(BeautifulSoup(html, "lxml"))


def test_parse_shared_living_room():
    assert _lr(_PAGE.format(where="in Bow", cls="tick", val="shared")) == "shared"


def test_parse_no_living_room():
    assert _lr(_PAGE.format(where="in Bow", cls="cross", val="No")) == "no"


def test_parse_missing_feature_list_is_unknown():
    # Markup change / no feature list at all → unknown, never "no" (fail-open).
    assert _lr("<html><body><p>no structured facts here</p></body></html>") == ""


def test_full_detail_parse_sets_field():
    listing = parse_listing_detail(
        _PAGE.format(where="in Bow", cls="cross", val="No"), "111", "u"
    )
    assert listing.living_room == "no"


def _score(living_room: str, cfg: AppConfig | None = None):
    cfg = cfg or AppConfig()
    cfg.filter.require_living_room = True  # these tests exercise the hard filter
    listing = Listing(
        id="1", url="u", price_pcm=1200, postcode="SE1 1AA", living_room=living_room
    )
    return score_listing(listing, _OK, cfg, distance_km=2.0, prefilter_too_far=False)


def test_defaults_do_not_drop_but_order_first():
    cfg = AppConfig()
    # Evaluation-friendly defaults: don't hide anything, just open shared first.
    assert cfg.filter.require_living_room is False
    assert cfg.daily.living_room_first is True


def test_explicit_no_is_dropped_when_filter_on():
    item = _score("no")
    assert item.filter_pass is False
    assert item.fail_reason == FailReason.NO_LIVING_ROOM


def test_shared_passes():
    item = _score("shared")
    assert item.filter_pass is True
    assert item.fail_reason == FailReason.OK


def test_unknown_is_kept_fail_open():
    item = _score("")
    assert item.filter_pass is True
    assert item.fail_reason == FailReason.OK


def test_default_off_keeps_no_lounge_flats():
    cfg = AppConfig()  # require_living_room defaults False
    listing = Listing(id="1", url="u", price_pcm=1200, postcode="SE1 1AA", living_room="no")
    item = score_listing(listing, _OK, cfg, distance_km=2.0, prefilter_too_far=False)
    assert item.filter_pass is True  # nothing dropped by default


def _si(lid: str, lr: str, avail: str | None = None) -> ScoredListing:
    return ScoredListing(listing=Listing(id=lid, url="u", living_room=lr), available_date=avail)


def _cfg(*, living_room_first=True, move_in_first=True, ideal="2026-09-01") -> AppConfig:
    cfg = AppConfig()
    cfg.daily.living_room_first = living_room_first
    cfg.daily.move_in_first = move_in_first
    cfg.preferences.ideal_move_in = ideal
    return cfg


def test_order_living_room_only_tiers_and_stability():
    # Living room primary; move-in off. shared→unknown→no, stable within each tier.
    items = [_si("a", "no"), _si("b", "shared"), _si("c", ""), _si("d", "shared"), _si("e", "no")]
    ordered = [s.listing.id for s in order_tabs(items, _cfg(move_in_first=False))]
    assert ordered == ["b", "d", "c", "a", "e"]


def test_move_in_orders_within_living_room_group():
    # All shared → living-room tier ties, so ordered by closeness to 2026-09-01.
    ideal = "2026-09-01"
    items = [
        _si("far", "shared", "2026-11-01"),   # ~61d after
        _si("on", "shared", "2026-09-01"),    # 0d
        _si("near", "shared", "2026-09-03"),  # 2d after
        _si("undated", "shared", None),       # unknown → last
    ]
    ordered = [s.listing.id for s in order_tabs(items, _cfg(ideal=ideal))]
    assert ordered == ["on", "near", "far", "undated"]


def test_living_room_beats_move_in():
    # A no-lounge room with a perfect date still sorts after a shared room, because
    # living room is the primary group and move-in is only the secondary sort.
    items = [
        _si("no_perfect_date", "no", "2026-09-01"),
        _si("shared_far_date", "shared", "2026-12-01"),
    ]
    ordered = [s.listing.id for s in order_tabs(items, _cfg())]
    assert ordered == ["shared_far_date", "no_perfect_date"]


def test_order_tabs_is_pure_no_drop():
    items = [_si("a", "no", "2026-09-01"), _si("b", "", None)]
    out = order_tabs(items, _cfg())
    assert len(out) == len(items)  # ordering only — never drops
