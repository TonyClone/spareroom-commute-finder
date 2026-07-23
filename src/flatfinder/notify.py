from __future__ import annotations

import html
import logging
import time

import httpx

from flatfinder.models import ScoredListing

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class NotifyError(RuntimeError):
    """A Telegram send failed after retries. Callers must NOT mark listings seen."""


def format_header(
    *,
    new_count: int,
    total_scraped: int,
    hard_pass: int,
    already_seen: int,
    tfl_unchecked: int = 0,
) -> str:
    """Digest header sent before the per-listing messages (HTML parse mode)."""
    if new_count:
        first = f"🏠 <b>Flatfinder — {new_count} new room{'s' if new_count != 1 else ''}</b>"
    else:
        first = "😴 <b>Flatfinder — no new rooms this run.</b> You're caught up."
    lines = [
        first,
        f"Scanned {total_scraped} · {hard_pass} pass budget+commute · {already_seen} already seen",
    ]
    if tfl_unchecked:
        lines.append(
            f"⚠️ {tfl_unchecked} room(s) unchecked (TfL limit) — this shortlist may be partial."
        )
    return "\n".join(lines)


def format_listing(s: ScoredListing, index: int, total: int) -> str:
    """One tappable message per room: facts on top, bare URL last so Telegram
    renders SpareRoom's link-preview card (photo included) under the text."""
    listing = s.listing
    title = html.escape(listing.title or "Room")

    facts: list[str] = []
    if listing.price_pcm:
        facts.append(f"£{listing.price_pcm:,.0f} pcm")
    j = s.journey
    if j and j.duration_minutes is not None:
        xfr = ""
        if j.transfers:
            xfr = f", {j.transfers} change{'s' if j.transfers != 1 else ''}"
        facts.append(f"🚇 {j.duration_minutes} min{xfr}")
    where = (listing.area or listing.postcode or "").strip()
    if where:
        facts.append(f"📍 {html.escape(where)}")

    extras: list[str] = []
    avail = (listing.available_from or s.available_date or "").strip()
    if avail:
        extras.append(f"📅 {html.escape(avail)}")
    lr = (listing.living_room or "").strip().lower()
    if lr == "no":
        extras.append("🛋 no living room")
    elif lr:
        extras.append("🛋 shared living room")

    lines = [f"<b>{index}/{total} · {title}</b>"]
    if facts:
        lines.append(" · ".join(facts))
    if extras:
        lines.append(" · ".join(extras))
    lines.append(listing.url)
    return "\n".join(lines)


class TelegramNotifier:
    """Minimal Telegram Bot API client (sendMessage only) — no extra deps.

    Create a bot once with @BotFather; DM it once so it may message you.
    Configured via TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (env or .env).
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout: float = 20.0,
        delay_seconds: float = 0.5,
        max_retries: int = 3,
    ):
        self.chat_id = chat_id
        self.delay_seconds = delay_seconds
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=f"{TELEGRAM_API}/bot{bot_token}", timeout=timeout
        )

    def close(self) -> None:
        self._client.close()

    def send_text(self, text: str, *, preview: bool = True) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview,
        }
        last_detail = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self._client.post("/sendMessage", json=payload)
            except httpx.HTTPError as e:
                last_detail = str(e)
                logger.warning("Telegram send error (attempt %s): %s", attempt, e)
                time.sleep(2**attempt)
                continue
            if r.status_code == 200:
                return
            # Rate limited: Telegram tells us how long to wait.
            if r.status_code == 429:
                try:
                    retry_after = int(r.json()["parameters"]["retry_after"])
                except Exception:
                    retry_after = 2**attempt
                logger.warning("Telegram 429 — waiting %ss", retry_after)
                time.sleep(retry_after + 1)
                continue
            last_detail = f"HTTP {r.status_code}: {r.text[:200]}"
            logger.warning("Telegram send failed (attempt %s): %s", attempt, last_detail)
            time.sleep(2**attempt)
        raise NotifyError(f"Telegram sendMessage failed after retries — {last_detail}")

    def send_shortlist(self, to_open: list[ScoredListing]) -> list[str]:
        """One message per room so each gets its own preview card. Returns the
        URLs actually delivered; raises NotifyError on the first hard failure so
        undelivered rooms are never marked seen."""
        sent: list[str] = []
        total = len(to_open)
        for i, s in enumerate(to_open, 1):
            self.send_text(format_listing(s, i, total))
            sent.append(s.listing.url)
            if i < total and self.delay_seconds > 0:
                time.sleep(self.delay_seconds)
        return sent
