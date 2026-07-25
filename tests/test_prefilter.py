from flatfinder.geo.prefilter import (
    estimate_pt_minutes,
    extract_outcode,
    outcode_is_hard_far,
    should_skip_tfl,
)

# Example office: Charing Cross, central London (matches config default)
OFFICE = (51.508362, -0.124639)


def test_extract_outcode_splits_inward_code():
    # Regression: "N1 7GU" compacts to "N17GU"; a greedy prefix grab used to
    # read the outcode as "N17G" and denylist central Angel rooms as Tottenham.
    assert extract_outcode("N1 7GU") == "N1"
    assert extract_outcode("SE1 2AA") == "SE1"
    assert extract_outcode("E1 3AA") == "E1"
    assert extract_outcode("W1A 1AA") == "W1A"
    assert extract_outcode("SE23 1AA") == "SE23"
    # Bare outcodes still come through whole.
    assert extract_outcode("N17") == "N17"
    assert extract_outcode("SE23") == "SE23"
    assert extract_outcode(None) is None


def test_single_digit_outcodes_not_denylisted():
    # Zone-1 postcodes whose inward code starts with an unlucky digit must not
    # prefix-match outer-London denylist entries (N17, SE12, E13, W13, SE19…).
    for pc in ("N1 7GU", "SE1 2AA", "E1 3AA", "W1 3AA", "SE1 9SG"):
        assert outcode_is_hard_far(extract_outcode(pc)) is False, pc
    r = should_skip_tfl(
        lat=51.532, lon=-0.106, postcode="N1 7GU",  # Angel, ~10 min from office
        office_lat=OFFICE[0], office_lon=OFFICE[1],
    )
    assert r.skip is False


def test_croydon_outcode_hard_far():
    assert outcode_is_hard_far("CR0") is True
    r = should_skip_tfl(
        lat=51.376, lon=-0.098, postcode="CR0 1AA",
        office_lat=OFFICE[0], office_lon=OFFICE[1],
    )
    assert r.skip is True


def test_shoreditch_not_skipped():
    r = should_skip_tfl(
        lat=51.525, lon=-0.08, postcode="E2 8AA",
        office_lat=OFFICE[0], office_lon=OFFICE[1],
        max_minutes=30,
    )
    assert r.skip is False
    assert r.est_minutes is not None and r.est_minutes < 40


def test_slow_sector_estimate_higher():
    # Same distance, SE-ish area should estimate higher than central N
    se = estimate_pt_minutes(12.0, area="SE", sector="S")
    n = estimate_pt_minutes(12.0, area="N", sector="N")
    assert se > n


def test_hard_km_cap():
    r = should_skip_tfl(
        lat=51.47, lon=-0.45, postcode="TW6 1AP",  # Heathrow-ish
        office_lat=OFFICE[0], office_lon=OFFICE[1],
        hard_max_km=22,
    )
    assert r.skip is True
