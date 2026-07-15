from __future__ import annotations

import re
from typing import Any

import httpx

POSTCODE_RE = re.compile(
    r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b",
    re.I,
)
OUTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\b", re.I)


def normalise_postcode(pc: str | None) -> str | None:
    if not pc:
        return None
    pc = pc.strip().upper()
    pc = re.sub(r"\s+", " ", pc)
    # Insert space before inward code if missing: EC1A1BB -> EC1A 1BB
    m = re.match(r"^([A-Z]{1,2}\d[A-Z\d]?)(\d[A-Z]{2})$", pc.replace(" ", ""))
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return pc


def extract_postcode(text: str) -> str | None:
    if not text:
        return None
    m = POSTCODE_RE.search(text)
    if m:
        return normalise_postcode(m.group(1))
    return None


class PostcodeClient:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=20.0)
        self._owns_client = client is None
        self._cache: dict[str, tuple[float, float] | None] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def geocode(self, postcode: str) -> tuple[float, float] | None:
        pc = normalise_postcode(postcode)
        if not pc:
            return None
        if pc in self._cache:
            return self._cache[pc]
        try:
            r = self._client.get(f"https://api.postcodes.io/postcodes/{pc.replace(' ', '')}")
            if r.status_code == 200:
                data: dict[str, Any] = r.json()
                result = data.get("result") or {}
                lat = result.get("latitude")
                lon = result.get("longitude")
                if lat is not None and lon is not None:
                    coords = (float(lat), float(lon))
                    self._cache[pc] = coords
                    return coords
            # Try outcode centroid
            outcode = pc.split()[0]
            r2 = self._client.get(f"https://api.postcodes.io/outcodes/{outcode}")
            if r2.status_code == 200:
                result = (r2.json().get("result") or {})
                lat = result.get("latitude")
                lon = result.get("longitude")
                if lat is not None and lon is not None:
                    coords = (float(lat), float(lon))
                    self._cache[pc] = coords
                    return coords
        except httpx.HTTPError:
            pass
        self._cache[pc] = None
        return None
