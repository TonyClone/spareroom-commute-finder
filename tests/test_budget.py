from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flatfinder.ai.budget import DailyBudgetTracker, TokenUsage, estimate_cost_usd


def test_estimate_cost():
    u = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    c = estimate_cost_usd(u, input_per_m=0.27, output_per_m=1.10)
    assert abs(c - 1.37) < 1e-9


def test_daily_cap(tmp_path: Path):
    t = DailyBudgetTracker(
        path=tmp_path / "b.json",
        daily_budget_usd=1.0,
        input_per_m=0.27,
        output_per_m=1.10,
    )
    assert t.can_afford(0.01)
    # Record enough to near $1
    # 0.27 * 2M in + 1.10 * 0.4M out = 0.54 + 0.44 = 0.98
    t.record(TokenUsage(prompt_tokens=2_000_000, completion_tokens=400_000))
    assert t.spent_today() > 0.9
    assert t.remaining() < 0.15
    assert not t.can_afford(0.5)


def test_concurrent_record_no_lost_updates(tmp_path: Path):
    """Parallel AI workers must not lose budget updates (thread-safe record)."""
    t = DailyBudgetTracker(
        path=tmp_path / "b.json",
        daily_budget_usd=100.0,
        input_per_m=0.27,
        output_per_m=1.10,
    )
    n = 200
    per_call = TokenUsage(prompt_tokens=1000, completion_tokens=100)
    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(lambda _: t.record(per_call), range(n)))
    expected = n * estimate_cost_usd(per_call, input_per_m=0.27, output_per_m=1.10)
    assert abs(t.spent_today() - expected) < 1e-9  # no lost updates
    day = t._data[t._today_key()]
    assert day["calls"] == n
    assert day["prompt_tokens"] == n * 1000
