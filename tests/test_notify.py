from flatfinder.models import JourneyResult, Listing, ScoredListing
from flatfinder.notify import format_header, format_listing


def _scored(**listing_kwargs) -> ScoredListing:
    defaults = dict(
        id="123",
        url="https://www.spareroom.co.uk/flatshare/flatshare_detail.pl?flatshare_id=123",
        title="Double room in sunny flatshare",
        price_pcm=1200.0,
        area="Bethnal Green",
        available_from="2026-08-01",
        living_room="shared",
    )
    defaults.update(listing_kwargs)
    return ScoredListing(
        listing=Listing(**defaults),
        journey=JourneyResult(duration_minutes=24, transfers=1),
    )


def test_format_listing_has_facts_and_bare_url_last():
    msg = format_listing(_scored(), 1, 3)
    lines = msg.split("\n")
    assert "1/3" in lines[0]
    assert "Double room in sunny flatshare" in lines[0]
    assert "£1,200 pcm" in msg
    assert "24 min, 1 change" in msg
    assert "Bethnal Green" in msg
    assert "shared living room" in msg
    # Bare URL on its own last line → Telegram renders the preview card
    assert lines[-1].startswith("https://www.spareroom.co.uk/")


def test_format_listing_escapes_html():
    msg = format_listing(_scored(title="Big <br> room & garden", area="<b>Zone 2</b>"), 1, 1)
    assert "<br>" not in msg
    assert "&lt;br&gt;" in msg
    assert "&amp;" in msg
    assert "<b>Zone 2</b>" not in msg


def test_format_listing_handles_missing_fields():
    s = ScoredListing(
        listing=Listing(id="1", url="https://example.com/1", title=""),
        journey=None,
    )
    msg = format_listing(s, 2, 2)
    assert "Room" in msg  # title fallback
    assert "£" not in msg
    assert "min" not in msg
    assert msg.endswith("https://example.com/1")


def test_format_listing_no_living_room():
    msg = format_listing(_scored(living_room="no"), 1, 1)
    assert "no living room" in msg


def test_format_header_new_rooms():
    h = format_header(new_count=3, total_scraped=214, hard_pass=41, already_seen=38)
    assert "3 new rooms" in h
    assert "214" in h and "41" in h and "38" in h
    assert "TfL" not in h


def test_format_header_singular_and_empty():
    assert "1 new room" in format_header(
        new_count=1, total_scraped=5, hard_pass=1, already_seen=0
    )
    assert "no new rooms" in format_header(
        new_count=0, total_scraped=5, hard_pass=1, already_seen=1
    )


def test_format_header_tfl_warning():
    h = format_header(
        new_count=2, total_scraped=10, hard_pass=4, already_seen=2, tfl_unchecked=7
    )
    assert "7" in h
    assert "partial" in h
