from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from flatfinder.config import HOME


class TflUsageTracker:
    """Persist TfL API call counts per calendar day (data/tfl_usage.json).

    TfL enforces a request quota; when we approach it we stop calling and flag
    the run INCOMPLETE rather than silently degrading results into NO_JOURNEY.
    Counts reset on the local calendar date (TfL's quota window may differ, but
    day-granularity is enough to know when we've run dry).
    """

    def __init__(self, path: Path | None = None, *, daily_limit: int = 500):
        self.path = path or (HOME / "data" / "tfl_usage.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.daily_limit = daily_limit
        self._data = self._load()
        self._lock = threading.RLock()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _key(self) -> str:
        return date.today().isoformat()

    def used_today(self) -> int:
        with self._lock:
            return int(self._data.get(self._key(), 0))

    def remaining(self) -> int:
        return max(0, self.daily_limit - self.used_today())

    def record(self, n: int = 1) -> int:
        """Add n calls to today's count (thread-safe). Returns new total."""
        with self._lock:
            k = self._key()
            self._data[k] = int(self._data.get(k, 0)) + n
            self._save()
            return self._data[k]

    def summary(self) -> str:
        return f"{self.used_today()}/{self.daily_limit} TfL calls today"
