from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


class HttpClient:
    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        use_proxy: bool = False,
        delay_seconds: float = 1.5,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        proxies: str | None = None
        if use_proxy and proxy_url:
            proxies = proxy_url
        self.delay_seconds = delay_seconds
        self.max_retries = max_retries
        self._last_request = 0.0
        # httpx 0.28+ uses proxy= not proxies=
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "headers": DEFAULT_HEADERS,
            "follow_redirects": True,
        }
        if proxies:
            kwargs["proxy"] = proxies
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                r = self._client.get(url, **kwargs)
                self._last_request = time.monotonic()
                if r.status_code in (403, 429, 503) and attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "HTTP %s for %s — retry in %ss", r.status_code, url, wait
                    )
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r
            except httpx.HTTPError as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise
        assert last_err
        raise last_err
