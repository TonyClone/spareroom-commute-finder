from __future__ import annotations

import re
from typing import Tuple

_PRICE_RE = re.compile(
    r"£\s*([\d,]+(?:\.\d+)?)\s*(pcm|pw|pm|p/?w|per\s*week|per\s*month|pcm)?",
    re.I,
)


def pw_to_pcm(pw: float) -> float:
    return round(pw * 52 / 12, 2)


def pcm_to_pw(pcm: float) -> float:
    return round(pcm * 12 / 52, 2)


def parse_price(text: str) -> Tuple[float | None, float | None, str]:
    """Return (price_pcm, price_pw, raw_snippet)."""
    if not text:
        return None, None, ""
    cleaned = " ".join(text.split())
    match = _PRICE_RE.search(cleaned)
    if not match:
        # No "£" anywhere → the price is genuinely unknown. Never guess from a
        # bare number ("2 double rooms" is not £2 pw!) — an invented price used
        # to trip the budget floor and hard-reject perfectly good listings.
        # Unknown prices are kept by design (see BudgetConfig).
        return None, None, cleaned[:80]

    value = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "").lower().replace(" ", "")
    raw = match.group(0)

    if unit in {"pw", "p/w", "perweek"}:
        return pw_to_pcm(value), value, raw
    if unit in {"pcm", "pm", "permonth"}:
        return value, pcm_to_pw(value), raw

    # Bare £ amount with context clues
    lower = cleaned.lower()
    if "pw" in lower or "per week" in lower or "/week" in lower:
        return pw_to_pcm(value), value, raw
    if "pcm" in lower or "per month" in lower or "/month" in lower:
        return value, pcm_to_pw(value), raw
    if value <= 800:
        return pw_to_pcm(value), value, raw
    return value, pcm_to_pw(value), raw
