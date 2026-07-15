from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum

# "14th Jul 2026", "Available Now", "ASAP", "1st August 2026", "2026-09-01"
_ORDINAL_DATE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"(?:\s+(\d{4}))?\b",
    re.I,
)
_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_NOW = re.compile(r"\b(available\s*now|asap|immediately|straight\s*away|now)\b", re.I)

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


class MoveFit(str, Enum):
    """Soft preference only — never used as a hard reject."""

    UNKNOWN = "unknown"  # couldn't parse — still show
    FLEXIBLE = "flexible"  # ASAP / now — easy to negotiate toward ideal
    GOOD = "good"  # available on/before ideal move-in
    OK = "ok"  # within a few weeks after ideal
    LATE = "late"  # well after ideal — still shown, just ranked lower
    EARLY_ONLY = "early_only"  # available now but might want longer stay before you move (info only)


def parse_available_date(text: str | None, *, today: date | None = None) -> date | None:
    """Best-effort parse of SpareRoom availability strings."""
    if not text:
        return None
    t = " ".join(text.strip().split())
    if not t:
        return None
    today = today or date.today()

    if _NOW.search(t):
        return today

    m = _ISO.search(t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    m = _ORDINAL_DATE.search(t)
    if m:
        day = int(m.group(1))
        mon = _MONTHS.get(m.group(2).lower()[:3]) or _MONTHS.get(m.group(2).lower())
        year = int(m.group(3)) if m.group(3) else today.year
        if mon is None:
            return None
        # If date is far in the past without year, bump to next year
        try:
            d = date(year, mon, min(day, 28) if mon == 2 else min(day, 30 if mon in (4, 6, 9, 11) else day))
            # Fix day more carefully
            d = date(year, mon, day)
        except ValueError:
            try:
                d = date(year, mon, 28)
            except ValueError:
                return None
        if not m.group(3) and d < today - __import__("datetime").timedelta(days=60):
            try:
                d = date(year + 1, mon, day)
            except ValueError:
                pass
        return d

    return None


def move_in_fit(
    available_text: str | None,
    ideal: date | None,
    *,
    today: date | None = None,
    late_grace_days: int = 21,
) -> tuple[MoveFit, date | None, str]:
    """
    Soft fit vs ideal move-in date.
    Never rejects — returns label + parsed date + short note for UI/ranking.
    """
    today = today or date.today()
    parsed = parse_available_date(available_text, today=today)

    if ideal is None:
        if parsed is None:
            return MoveFit.UNKNOWN, None, "no ideal date set"
        return MoveFit.FLEXIBLE, parsed, f"available {parsed.isoformat()}"

    if parsed is None:
        return MoveFit.UNKNOWN, None, "date unclear — still show (negotiate)"

    if parsed <= today + __import__("datetime").timedelta(days=7):
        # Available now / very soon — you can negotiate hold until ~ideal
        return MoveFit.FLEXIBLE, parsed, f"from {parsed.isoformat()} (negotiate toward {ideal.isoformat()})"

    if parsed <= ideal:
        return MoveFit.GOOD, parsed, f"available {parsed.isoformat()} ≤ ideal {ideal.isoformat()}"

    delta = (parsed - ideal).days
    if delta <= late_grace_days:
        return MoveFit.OK, parsed, f"{delta}d after ideal — still fine to ask"
    return MoveFit.LATE, parsed, f"{delta}d after ideal — show anyway, negotiate"


def soft_rank_penalty(fit: MoveFit) -> float:
    """Small additive rank penalty (lower rank_score is better). Never filters out."""
    return {
        MoveFit.FLEXIBLE: 0.0,
        MoveFit.GOOD: 0.5,
        MoveFit.OK: 5.0,
        MoveFit.LATE: 25.0,
        MoveFit.UNKNOWN: 3.0,
        MoveFit.EARLY_ONLY: 1.0,
    }.get(fit, 3.0)


def fit_badge(fit: MoveFit) -> str:
    return {
        MoveFit.FLEXIBLE: "flex",
        MoveFit.GOOD: "good",
        MoveFit.OK: "ok",
        MoveFit.LATE: "late",
        MoveFit.UNKNOWN: "?",
        MoveFit.EARLY_ONLY: "early",
    }.get(fit, "?")
