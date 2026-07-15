from pathlib import Path

from flatfinder.db import Database
from flatfinder.geo.prefilter import radius_km_for_minutes, radius_miles_for_minutes
from flatfinder.scraper.parse import extract_search_id, next_page_href

# Mirrors real SpareRoom results markup: a query-only relative list-mode "next"
# link plus a competing map-view offset link (which must be ignored).
SAMPLE = """
<html><body>
<a href="/flatshare/flatshare_detail.pl?flatshare_id=111&search_id=555">Room A £300pw</a>
<a href="/flatshare/?search_id=555&max_per_page=20&show_results=as+a+map&offset=20">Map</a>
<a href="?offset=20&search_id=555&sort_by=by_day&mode=list&max_per_page=20" class="next">Next &raquo;</a>
</body></html>
"""

PAGE_URL = "https://www.spareroom.co.uk/flatshare/?search_id=555&mode=list&offset=0"


def test_extract_search_id():
    assert extract_search_id(SAMPLE) == "555"
    assert extract_search_id("<html>no id here</html>") is None
    assert extract_search_id("search_id=0") is None  # empty/placeholder id


def test_next_page_href_prefers_list_mode_and_resolves_relative():
    nxt = next_page_href(SAMPLE, current_offset=0, base_url=PAGE_URL)
    assert nxt is not None
    # Resolved against the page URL's /flatshare/ path, not the bare host
    assert nxt.startswith("https://www.spareroom.co.uk/flatshare/?")
    assert "offset=20" in nxt and "search_id=555" in nxt and "mode=list" in nxt
    # The map-view link must never be chosen
    assert "as+a+map" not in nxt


def test_next_page_href_skips_map_only_pages():
    html = '<a href="/flatshare/?search_id=5&show_results=as+a+map&offset=20">map</a>'
    assert next_page_href(html, current_offset=0) is None


def test_next_page_href_none_when_no_forward_offset():
    html = '<a href="?offset=0&search_id=5&mode=list">page1</a>'
    assert next_page_href(html, current_offset=20) is None


def test_radius_scales_with_commute_budget():
    # Derived from the fastest-corridor PT model, whole miles.
    assert radius_miles_for_minutes(20, 1.35) == 6
    assert radius_miles_for_minutes(30, 1.35) == 10
    assert radius_miles_for_minutes(45, 1.35) == 16
    # A tighter budget yields a smaller net than a looser one.
    assert radius_miles_for_minutes(20) < radius_miles_for_minutes(45)
    assert abs(radius_km_for_minutes(30, 1.35) - 15.0) < 0.1


def test_radius_clamped():
    assert radius_miles_for_minutes(1) == 2       # floor
    assert radius_miles_for_minutes(100000) == 40  # SpareRoom's ceiling


def test_scanned_watermark(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    assert db.scanned_count() == 0
    assert db.have_scanned_ids(["a", "b"]) == set()

    assert db.mark_scanned(["a", "b"]) == 2
    assert db.scanned_count() == 2
    assert db.have_scanned_ids(["a", "c"]) == {"a"}

    # Re-scanning an existing id bumps its counter, doesn't duplicate the row.
    db.mark_scanned(["a"])
    assert db.scanned_count() == 2
