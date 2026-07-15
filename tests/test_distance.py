from flatfinder.geo.distance import haversine_km, is_way_too_far

# Example office: Charing Cross, central London (matches config default)
OFFICE = (51.508362, -0.124639)


def test_soho_to_nearby_not_far():
    # Roughly Shoreditch-ish
    too_far, km = is_way_too_far(51.525, -0.08, *OFFICE, max_km=18)
    assert too_far is False
    assert km is not None and km < 10


def test_heathrow_way_too_far():
    # Heathrow ~23 km crow-flies from Soho
    too_far, km = is_way_too_far(51.47, -0.4543, *OFFICE, max_km=18)
    assert too_far is True
    assert km is not None and km > 18


def test_missing_coords_not_filtered():
    too_far, km = is_way_too_far(None, None, *OFFICE, max_km=18)
    assert too_far is False
    assert km is None


def test_haversine_symmetry():
    a = haversine_km(51.5, -0.1, 51.6, -0.2)
    b = haversine_km(51.6, -0.2, 51.5, -0.1)
    assert abs(a - b) < 1e-6
