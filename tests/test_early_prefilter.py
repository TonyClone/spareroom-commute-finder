from flatfinder.geo.prefilter import (
    parse_card_location,
    parse_card_price,
    should_skip_search_card,
)


def test_parse_card_location_se23():
    name, oc = parse_card_location("Cosy room Forest Hill (SE23) £780 pcm")
    assert oc == "SE23"
    assert name and "forest" in name.lower()


def test_parse_card_price_ignores_photo_count():
    text = "FREE TO CONTACT New today 9 photos Double Room Battersea (SW8) £1,300 pcm - bills"
    pcm, pw, raw = parse_card_price(text)
    assert pcm == 1300
    assert "1,300" in raw or "1300" in raw.replace(",", "")


def test_early_skip_forest_hill_outcode():
    card = {
        "id": "1",
        "title": "Room Forest Hill",
        "snippet": "Forest Hill (SE23) £780 pcm",
        "price_pcm": 780,
    }
    r = should_skip_search_card(card, max_pcm=1450)
    assert r.skip is True
    assert "SE23" in r.reason or "denylist" in r.reason.lower() or "forest" in r.reason.lower()


def test_early_skip_over_budget():
    card = {
        "id": "2",
        "title": "Luxury Shoreditch",
        "snippet": "Shoreditch (E1) £2,000 pcm",
        "price_pcm": 2000,
    }
    r = should_skip_search_card(card, max_pcm=1450)
    assert r.skip is True
    assert "budget" in r.reason.lower()


def test_early_keep_bethnal_green():
    card = {
        "id": "3",
        "title": "Bright room",
        "snippet": "Bethnal Green (E2) £1,100 pcm Double room",
        "price_pcm": 1100,
        "area": "Bethnal Green",
        "postcode": "E2",
    }
    r = should_skip_search_card(card, max_pcm=1450)
    assert r.skip is False


def test_early_skip_croydon_name():
    card = {
        "id": "4",
        "title": "Room in Croydon",
        "snippet": "Nice double in Croydon town centre £900 pcm",
        "price_pcm": 900,
    }
    r = should_skip_search_card(card, max_pcm=1450)
    assert r.skip is True
