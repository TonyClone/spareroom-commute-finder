from flatfinder.prices import parse_price, pcm_to_pw, pw_to_pcm


def test_pw_conversion():
    assert pw_to_pcm(335) == round(335 * 52 / 12, 2)
    assert abs(pcm_to_pw(1450) - 1450 * 12 / 52) < 0.01


def test_parse_weekly():
    pcm, pw, raw = parse_price("£300 pw double room")
    assert pw == 300
    assert pcm == pw_to_pcm(300)
    assert "300" in raw


def test_parse_monthly():
    pcm, pw, raw = parse_price("Rent £1200 pcm")
    assert pcm == 1200
    assert pw == pcm_to_pw(1200)


def test_parse_bare_heuristic():
    pcm, pw, _ = parse_price("£250")
    assert pw == 250
