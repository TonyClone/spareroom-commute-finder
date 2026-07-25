"""Detect listings that are unambiguously short-term-only sublets.

Deliberately conservative + fail-open, matching the living-room filter's
philosophy: a room is only dropped when the ad makes it EXTREMELY clear it's a
short let — a structured "Maximum term" at or under the threshold, or wording
like "short term only" / "sublet". Anything ambiguous ("short or long term
welcome", SpareRoom's standard "Short term let considered?" field, a missing
max term) is always kept.
"""

from __future__ import annotations

import re

from flatfinder.models import Listing

# "3 months" / "12 month" / "6 weeks" → months. First number wins.
_MONTHS_RE = re.compile(r"(\d+)\s*month", re.I)
_WEEKS_RE = re.compile(r"(\d+)\s*week", re.I)

# Phrases that on their own say "this is exclusively a short let" — safe to
# match even inside a long description because of the explicit "only"/"max".
_EXPLICIT_ONLY_RE = re.compile(
    r"short[\s-]*(?:term|let)s?[\s-]*(?:let\s+)?only"  # short term only / short lets only
    r"|only\s+(?:a\s+)?short[\s-]*(?:term|let)"        # only a short let
    r"|no\s+long[\s-]*term"                            # no long term (tenants)
    r"|temporary\s+(?:let|sublet)\b",
    re.I,
)

# Words that are clear enough in a TITLE (short + prominent) but too risky to
# match in a description, where "no subletting allowed" or "short term let
# considered" would false-positive.
_TITLE_ONLY_RE = re.compile(
    r"\bsub[\s-]*let(?:ting)?\b"
    r"|\bshort[\s-]*(?:term|let)\b"
    r"|\bholiday\s+let\b"
    r"|\bsummer\s+(?:let|sublet)\b",
    re.I,
)

# If the ad also mentions long-term-friendly wording, it isn't "extremely
# clear" any more — keep it.
_LONG_TERM_RE = re.compile(r"\blong[\s-]*term\b|\bno\s+sub[\s-]*let", re.I)


def parse_term_months(text: str | None) -> float | None:
    """'3 months' → 3.0, '6 weeks' → ~1.4, 'None'/blank/unparsable → None."""
    if not text:
        return None
    m = _MONTHS_RE.search(text)
    if m:
        return float(m.group(1))
    w = _WEEKS_RE.search(text)
    if w:
        return float(w.group(1)) * 12 / 52
    return None


def is_short_term_only(listing: Listing, *, max_months: int = 3) -> tuple[bool, str]:
    """(should_drop, reason). Fail-open: unknown/ambiguous → (False, "")."""
    # 1) Structured "Maximum term" field — the strongest signal there is.
    months = parse_term_months(listing.max_term)
    if months is not None and 0 < months <= max_months:
        return True, f"max term {listing.max_term.strip()}"

    title = listing.title or ""
    description = listing.description or ""

    # 2) Explicit "short term only"-style wording anywhere in the ad.
    for text in (title, description):
        m = _EXPLICIT_ONLY_RE.search(text)
        if m:
            return True, f"'{m.group(0).strip()}'"

    # 3) Prominent short-let/sublet wording in the title — unless the ad also
    #    talks long-term, in which case it isn't unambiguous.
    m = _TITLE_ONLY_RE.search(title)
    if m and not _LONG_TERM_RE.search(title) and not _LONG_TERM_RE.search(description):
        return True, f"'{m.group(0).strip()}' in title"

    return False, ""
