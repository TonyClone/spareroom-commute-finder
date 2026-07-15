from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from flatfinder.models import JourneyResult

logger = logging.getLogger(__name__)

TFL_BASE = "https://api.tfl.gov.uk"


def next_weekday(from_date: date | None = None) -> date:
    d = from_date or date.today()
    # If weekend, jump to Monday; if weekday after morning maybe still use today
    if d.weekday() >= 5:
        d = d + timedelta(days=(7 - d.weekday()))
    return d


def resolve_journey_date(spec: str) -> date:
    spec = (spec or "next_weekday").strip().lower()
    if spec == "today":
        return date.today()
    if spec == "next_weekday":
        d = date.today()
        # Prefer tomorrow if after 10am? Keep simple: next weekday from today (today if weekday)
        if d.weekday() >= 5:
            return next_weekday(d)
        return d
    return date.fromisoformat(spec)


def format_tfl_time(hhmm: str) -> str:
    """'09:00' -> '0900'."""
    cleaned = hhmm.strip().replace(":", "")
    if len(cleaned) == 3:
        cleaned = "0" + cleaned
    return cleaned[:4]


def format_tfl_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def journey_cache_key(
    origin_lat: float | None,
    origin_lon: float | None,
    origin_postcode: str | None,
    dest_lat: float,
    dest_lon: float,
    arrive_time: str,
    journey_date: str,
) -> str:
    if origin_lat is not None and origin_lon is not None:
        o = f"{origin_lat:.4f},{origin_lon:.4f}"
    else:
        o = (origin_postcode or "unknown").replace(" ", "").upper()
    return f"{o}|{dest_lat:.4f},{dest_lon:.4f}|{arrive_time}|{journey_date}|Arriving"


def _leg_mode(leg: dict[str, Any]) -> str:
    mode = leg.get("mode") or {}
    if isinstance(mode, dict):
        return str(mode.get("name") or mode.get("id") or "unknown")
    return str(mode or "unknown")


def parse_journey_payload(data: dict[str, Any]) -> JourneyResult:
    journeys = data.get("journeys") or []
    if not journeys:
        # Disambiguation or empty
        if data.get("fromLocationDisambiguation") or data.get("toLocationDisambiguation"):
            return JourneyResult(
                status="ERROR",
                error="Location disambiguation required; use lat/lon",
            )
        return JourneyResult(status="UNREACHABLE", error="No journeys returned")

    best = min(journeys, key=lambda j: int(j.get("duration") or 10**9))
    duration = int(best.get("duration") or 0)
    legs = best.get("legs") or []
    walk_minutes = 0
    transit_legs = 0
    summaries: list[str] = []

    for leg in legs:
        mode = _leg_mode(leg).lower()
        leg_dur = int(leg.get("duration") or 0)
        instruction = ""
        instr = leg.get("instruction") or {}
        if isinstance(instr, dict):
            instruction = str(instr.get("summary") or instr.get("detailed") or "")
        if mode == "walking":
            walk_minutes += leg_dur
            if instruction:
                summaries.append(f"walk {leg_dur}m")
            else:
                summaries.append(f"walk {leg_dur}m")
        else:
            transit_legs += 1
            line = ""
            route = leg.get("routeOptions") or []
            if route and isinstance(route, list):
                name = route[0].get("name") if isinstance(route[0], dict) else ""
                line = str(name or "")
            label = line or mode
            summaries.append(f"{label} {leg_dur}m" if label else f"{mode} {leg_dur}m")

    # Transfers ≈ transit vehicle boards minus 1
    transfers = max(transit_legs - 1, 0) if transit_legs else 0
    summary = " → ".join(summaries) if summaries else f"{duration} min total"

    return JourneyResult(
        duration_minutes=duration,
        transfers=transfers,
        walk_minutes=walk_minutes,
        summary=summary,
        legs_json=legs if isinstance(legs, list) else [],
        status="OK",
    )


class TflJourneyClient:
    def __init__(
        self,
        app_key: str = "",
        app_id: str = "",
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ):
        # A key is OPTIONAL. TfL's Unified API answers unauthenticated requests at a
        # lower rate limit, which is plenty for a personal daily hunt (journeys are
        # cached, so steady-state runs make very few calls). If you do hit a 429 it
        # surfaces as an INCOMPLETE run rather than a crash — add a free key then.
        self.app_key = app_key
        self.app_id = app_id
        self._client = client or httpx.Client(timeout=timeout, base_url=TFL_BASE)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if self.app_key:
            p["app_key"] = self.app_key
        if self.app_id:
            p["app_id"] = self.app_id
        if extra:
            p.update(extra)
        return p

    def journey(
        self,
        *,
        from_lat: float | None = None,
        from_lon: float | None = None,
        from_postcode: str | None = None,
        to_lat: float,
        to_lon: float,
        time_hhmm: str = "09:00",
        date_spec: str = "next_weekday",
        time_is: str = "Arriving",
    ) -> JourneyResult:
        if from_lat is not None and from_lon is not None:
            origin = f"{from_lat},{from_lon}"
        elif from_postcode:
            origin = from_postcode.replace(" ", "")
        else:
            return JourneyResult(status="ERROR", error="No origin coordinates or postcode")

        dest = f"{to_lat},{to_lon}"
        jdate = resolve_journey_date(date_spec)
        tfl_time = format_tfl_time(time_hhmm)
        tfl_date = format_tfl_date(jdate)

        params = self._params(
            {
                "time": tfl_time,
                "date": tfl_date,
                "timeIs": time_is,
                "journeyPreference": "LeastTime",
                "mode": "tube,bus,overground,dlr,elizabeth-line,national-rail,tram,walking",
            }
        )

        # Correct Unified API path (NOT /Journey/{from}/to/{to} — that 404s).
        path = f"/Journey/JourneyResults/{origin}/to/{dest}"
        try:
            r = self._client.get(path, params=params)
            if r.status_code == 300:
                # Disambiguation — retry if we used postcode without coords
                data = r.json()
                return JourneyResult(
                    status="ERROR",
                    error="Disambiguation: " + str(data.get("message") or "ambiguous location"),
                )
            if r.status_code == 429:
                # Rate/quota limit — distinct from a genuine routing failure so
                # the pipeline can flag the run INCOMPLETE rather than "no route".
                return JourneyResult(
                    status="RATE_LIMITED",
                    error=f"TfL 429 rate/quota limit: {r.text[:120]}",
                )
            if r.status_code >= 400:
                return JourneyResult(
                    status="ERROR",
                    error=f"TfL HTTP {r.status_code}: {r.text[:200]}",
                )
            data = r.json()
            result = parse_journey_payload(data)
            return result
        except httpx.HTTPError as e:
            logger.warning("TfL request failed: %s", e)
            return JourneyResult(status="ERROR", error=str(e))
