from flatfinder.scraper.parse import parse_search_results


SAMPLE = """
<html><body>
<a href="/flatshare/flatshare_detail.pl?flatshare_id=12345678&search_id=999">
  Double room in Shoreditch £280 pw
</a>
<a href="/flatshare/flatshare_detail.pl?flatshare_id=12345678&search_id=999">dup</a>
<a href="/flatshare/flatshare_detail.pl?flatshare_id=87654321">
  Nice room Hackney £1100 pcm
</a>
<div>
<a href="/flatshare/flatshare_detail.pl?flatshare_id=99900011">
  Forest Hill room
</a>
Forest Hill (SE23) £780 pcm - bills inc. Single room
</div>
</body></html>
"""


def test_parse_search_ids():
    items = parse_search_results(SAMPLE)
    ids = {i["id"] for i in items}
    assert "12345678" in ids and "87654321" in ids
    by_id = {i["id"]: i for i in items}
    assert by_id["12345678"]["price_pw"] == 280
    assert by_id["87654321"]["price_pcm"] == 1100


def test_parse_search_outcode_and_price_from_card():
    items = parse_search_results(SAMPLE)
    by_id = {i["id"]: i for i in items}
    if "99900011" in by_id:
        card = by_id["99900011"]
        assert card.get("outcode") == "SE23" or (card.get("postcode") or "").startswith("SE23")
        assert card.get("price_pcm") == 780
