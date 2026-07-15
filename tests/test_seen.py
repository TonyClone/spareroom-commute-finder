from pathlib import Path

from flatfinder.db import Database


def test_seen_dedup(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    assert db.seen_count() == 0
    db.mark_seen(["a", "b"], opened=True, source="daily")
    assert db.seen_count() == 2
    assert db.is_seen("a")
    assert not db.is_seen("c")
    unseen = db.filter_unseen_ids(["a", "b", "c", "d"])
    assert unseen == {"c", "d"}
    n = db.clear_seen()
    assert n == 2
    assert db.seen_count() == 0
