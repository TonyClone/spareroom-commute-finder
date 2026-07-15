from flatfinder.geo.prefilter import (
    estimate_pt_minutes,
    outcode_is_hard_far,
    should_skip_tfl,
)

# Example office: Charing Cross, central London (matches config default)
OFFICE = (51.508362, -0.124639)


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
