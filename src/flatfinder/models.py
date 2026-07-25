from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FailReason(str, Enum):
    OK = "OK"
    OVER_BUDGET = "OVER_BUDGET"
    UNDER_BUDGET = "UNDER_BUDGET"  # below budget.min_pcm floor
    NO_LIVING_ROOM = "NO_LIVING_ROOM"  # detail field explicitly says no living room
    SHORT_TERM = "SHORT_TERM"  # unambiguously a short-term-only sublet
    OVER_COMMUTE = "OVER_COMMUTE"
    TOO_FAR = "TOO_FAR"  # rough crow-flies prefilter before TfL
    NO_LOCATION = "NO_LOCATION"
    UNREACHABLE = "UNREACHABLE"
    NO_JOURNEY = "NO_JOURNEY"
    TFL_LIMIT = "TFL_LIMIT"  # not evaluated — TfL daily quota reached / rate-limited
    AI_REJECTED = "AI_REJECTED"
    ALREADY_SEEN = "ALREADY_SEEN"
    OTHER = "OTHER"


class AIVerdict(BaseModel):
    keep: bool = True
    score: int = 5  # 1-10 quality for living
    reasons: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    summary: str = ""
    error: str = ""


class Listing(BaseModel):
    id: str
    url: str
    title: str = ""
    price_raw: str = ""
    price_pcm: float | None = None
    price_pw: float | None = None
    postcode: str | None = None
    lat: float | None = None
    lon: float | None = None
    geo_confidence: str = "unknown"  # exact | postcode | outcode | unknown
    area: str = ""
    room_type: str = ""
    # "" = unknown (field absent / unparsed) · "no" = explicitly no living room ·
    # otherwise the SpareRoom value ("shared", "own", …). Fail-open: unknown is
    # never treated as "no".
    living_room: str = ""
    bills_included: bool | None = None
    available_from: str = ""
    # SpareRoom's structured "Maximum term" fact, verbatim ("3 months", "None",
    # ""). "" = unknown; the short-term filter fails open on it.
    max_term: str = ""
    # "Minimum term" verbatim — informational for now.
    min_term: str = ""
    nearest_station: str = ""
    description: str = ""
    image_url: str = ""
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    raw: dict[str, Any] = Field(default_factory=dict)


class JourneyResult(BaseModel):
    duration_minutes: int | None = None
    transfers: int | None = None
    walk_minutes: int | None = None
    summary: str = ""
    legs_json: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "OK"  # OK | UNREACHABLE | ERROR
    error: str = ""
    cache_key: str = ""


class ScoredListing(BaseModel):
    listing: Listing
    journey: JourneyResult | None = None
    filter_pass: bool = False
    fail_reason: FailReason = FailReason.OTHER
    rank_score: float = 0.0
    ai: AIVerdict | None = None
    already_seen: bool = False
    # Soft move-in preference (never used as hard reject when soft_only)
    move_fit: str = "unknown"  # flexible|good|ok|late|unknown
    move_note: str = ""
    available_date: str | None = None
