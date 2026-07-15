from pathlib import Path

from flatfinder.commute.usage import TflUsageTracker
from flatfinder.config import AppConfig
from flatfinder.models import FailReason, JourneyResult, Listing
from flatfinder.rank import score_listing


def test_usage_tracker_cap_and_persist(tmp_path: Path):
    p = tmp_path / "tfl_usage.json"
    t = TflUsageTracker(path=p, daily_limit=10)
    assert t.used_today() == 0
    assert t.remaining() == 10
    t.record(4)
    assert t.used_today() == 4
    assert t.remaining() == 6
    t.record(6)
    assert t.remaining() == 0  # exactly hit the cap
    t.record(5)  # over-record doesn't go negative on remaining()
    assert t.remaining() == 0
    # Persists across instances (same calendar day)
    t2 = TflUsageTracker(path=p, daily_limit=10)
    assert t2.used_today() == 15


def test_rate_limited_journey_scores_as_tfl_limit():
    cfg = AppConfig()
    listing = Listing(id="1", url="u", title="Room", price_pcm=1000, postcode="SE1 1AA")
    rl = JourneyResult(status="RATE_LIMITED", error="TfL 429")
    item = score_listing(listing, rl, cfg, distance_km=2.0, prefilter_too_far=False)
    assert item.filter_pass is False
    assert item.fail_reason == FailReason.TFL_LIMIT


def test_ok_journey_still_passes_under_cap():
    cfg = AppConfig()  # max_minutes default 30
    listing = Listing(id="2", url="u", title="Room", price_pcm=1000, postcode="SE1 1AA")
    ok = JourneyResult(status="OK", duration_minutes=20, transfers=1)
    item = score_listing(listing, ok, cfg, distance_km=2.0, prefilter_too_far=False)
    assert item.filter_pass is True
    assert item.fail_reason == FailReason.OK
