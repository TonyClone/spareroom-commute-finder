from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from flatfinder.config import HOME


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def estimate_cost_usd(
    usage: TokenUsage,
    *,
    input_per_m: float,
    output_per_m: float,
) -> float:
    """Cost in USD from token counts and per-million rates."""
    return (
        usage.prompt_tokens / 1_000_000.0 * input_per_m
        + usage.completion_tokens / 1_000_000.0 * output_per_m
    )


class DailyBudgetTracker:
    """
    Persist approximate DeepSeek spend per calendar day (local date).
    File: data/ai_budget.json
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        daily_budget_usd: float = 1.0,
        input_per_m: float = 0.27,
        output_per_m: float = 1.10,
    ):
        self.path = path or (HOME / "data" / "ai_budget.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.daily_budget_usd = daily_budget_usd
        self.input_per_m = input_per_m
        self.output_per_m = output_per_m
        self._data = self._load()
        # Reentrant so can_afford() → remaining() → spent_today() don't deadlock
        # when the AI pass calls them from multiple worker threads.
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

    def _today_key(self) -> str:
        return date.today().isoformat()

    def spent_today(self) -> float:
        with self._lock:
            day = self._data.get(self._today_key()) or {}
            return float(day.get("usd", 0.0))

    def remaining(self) -> float:
        return max(0.0, self.daily_budget_usd - self.spent_today())

    def can_afford(self, estimated_call_usd: float = 0.002) -> bool:
        if self.daily_budget_usd <= 0:
            return False
        with self._lock:
            return self.remaining() >= estimated_call_usd * 0.5  # small buffer

    def record(self, usage: TokenUsage) -> float:
        """Add usage cost; return cost of this call. Thread-safe."""
        cost = estimate_cost_usd(
            usage,
            input_per_m=self.input_per_m,
            output_per_m=self.output_per_m,
        )
        with self._lock:
            key = self._today_key()
            day = self._data.get(key) or {
                "usd": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "calls": 0,
            }
            day["usd"] = float(day["usd"]) + cost
            day["prompt_tokens"] = int(day["prompt_tokens"]) + usage.prompt_tokens
            day["completion_tokens"] = int(day["completion_tokens"]) + usage.completion_tokens
            day["calls"] = int(day["calls"]) + 1
            self._data[key] = day
            self._save()
        return cost

    def summary(self) -> str:
        with self._lock:
            day = self._data.get(self._today_key()) or {}
        return (
            f"${self.spent_today():.4f} / ${self.daily_budget_usd:.2f} today · "
            f"{day.get('calls', 0)} AI calls · "
            f"{day.get('prompt_tokens', 0)}+{day.get('completion_tokens', 0)} tokens"
        )
