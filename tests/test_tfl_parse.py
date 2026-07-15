from flatfinder.commute.tfl import parse_journey_payload


def test_parse_simple_journey():
    payload = {
        "journeys": [
            {
                "duration": 24,
                "legs": [
                    {
                        "mode": {"name": "walking"},
                        "duration": 6,
                        "instruction": {"summary": "Walk to station"},
                    },
                    {
                        "mode": {"name": "tube"},
                        "duration": 12,
                        "routeOptions": [{"name": "Northern"}],
                        "instruction": {"summary": "Northern line"},
                    },
                    {
                        "mode": {"name": "walking"},
                        "duration": 6,
                        "instruction": {"summary": "Walk to destination"},
                    },
                ],
            }
        ]
    }
    result = parse_journey_payload(payload)
    assert result.status == "OK"
    assert result.duration_minutes == 24
    assert result.transfers == 0
    assert result.walk_minutes == 12
    assert "Northern" in result.summary or "tube" in result.summary.lower()


def test_empty_journeys():
    result = parse_journey_payload({"journeys": []})
    assert result.status == "UNREACHABLE"
