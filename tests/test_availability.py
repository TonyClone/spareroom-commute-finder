from datetime import date

from flatfinder.availability import move_in_fit, parse_available_date, soft_rank_penalty, MoveFit


def test_parse_now():
    assert parse_available_date("Available Now", today=date(2026, 7, 11)) == date(2026, 7, 11)


def test_parse_ordinal():
    d = parse_available_date("14th Jul 2026")
    assert d == date(2026, 7, 14)


def test_soft_never_rejects_late():
    fit, parsed, note = move_in_fit(
        "1st October 2026",
        date(2026, 8, 27),
        today=date(2026, 7, 11),
    )
    assert fit == MoveFit.LATE
    assert parsed == date(2026, 10, 1)
    assert "negotiate" in note.lower() or "after" in note.lower()
    # ranking penalty exists but we still would show the listing
    assert soft_rank_penalty(fit) > soft_rank_penalty(MoveFit.GOOD)


def test_good_before_ideal():
    fit, parsed, _ = move_in_fit(
        "20th Aug 2026",
        date(2026, 8, 27),
        today=date(2026, 7, 11),
    )
    assert fit == MoveFit.GOOD
    assert parsed == date(2026, 8, 20)


def test_unknown_still_ok():
    fit, parsed, note = move_in_fit("tbc soon", date(2026, 8, 27))
    assert fit == MoveFit.UNKNOWN
    assert parsed is None
    assert "negotiate" in note.lower() or "unclear" in note.lower()
