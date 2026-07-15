from flatfinder.ai.deepseek import _parse_verdict


def test_parse_clean_json():
    v = _parse_verdict('{"keep": false, "score": 2, "reasons": ["scam"], "red_flags": ["whatsapp only"], "summary": "skip"}')
    assert v.keep is False
    assert v.score == 2
    assert "scam" in v.reasons
    assert "whatsapp" in v.red_flags[0].lower()


def test_parse_fenced():
    v = _parse_verdict('```json\n{"keep": true, "score": 8, "reasons": [], "red_flags": [], "summary": "fine"}\n```')
    assert v.keep is True
    assert v.score == 8


def test_parse_fail_open():
    v = _parse_verdict("not json at all")
    assert v.keep is True
    assert v.error
