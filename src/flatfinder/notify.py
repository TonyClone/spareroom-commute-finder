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

    def send_text(
        self,
        text: str,
        *,
        preview: bool = True,
        silent: bool = False,
        chat_id: str | None = None,
        reply_markup: dict | None = None,
    ) -> None:
        payload: dict = {
            "chat_id": chat_id or self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview,
            # silent=True delivers without a notification buzz — used for
            # "no new rooms" digests so frequent runs never feel like spam.
            "disable_notification": silent,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
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

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Acknowledge a menu-button tap. Single attempt, fail-open: taps are
        processed at the NEXT run, so Telegram usually reports the query as
        expired — the applied change and the edited menu card are the real
        feedback, this is just best-effort UI polish."""
        try:
            self._client.post(
                "/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text[:190]},
            )
        except httpx.HTTPError as e:
            logger.debug("answerCallbackQuery failed (expected for old taps): %s", e)

    def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        *,
        reply_markup: dict | None = None,
    ) -> bool:
        """Edit a previously sent message (used to refresh a menu card in
        place). Single attempt, fail-open — returns False on any failure so
        the caller can fall back to sending a fresh card."""
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            r = self._client.post("/editMessageText", json=payload)
        except httpx.HTTPError as e:
            logger.warning("editMessageText failed: %s", e)
            return False
        if r.status_code != 200:
            logger.info("editMessageText HTTP %s: %s", r.status_code, r.text[:150])
            return False
        return True

    def get_updates(self, offset: int | None = None, limit: int = 100) -> list[dict]:
        """Fetch queued bot updates (used by the remote-settings console).

        Fail-open: any API/network problem returns [] so a settings poll can
        never break the hunt itself. No long-polling — we only drain what's
        already queued since the last run."""
        payload: dict = {
            "timeout": 0,
            "limit": limit,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        try:
            r = self._client.post("/getUpdates", json=payload)
            if r.status_code != 200:
                logger.warning("Telegram getUpdates HTTP %s: %s", r.status_code, r.text[:200])
                return []
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Telegram getUpdates failed: %s", e)
            return []
        if not data.get("ok"):
            logger.warning("Telegram getUpdates not ok: %s", str(data)[:200])
            return []
        return data.get("result") or []

    def send_shortlist(
        self, to_open: list[ScoredListing], *, chat_id: str | None = None
    ) -> list[str]:
        """One message per room so each gets its own preview card. Returns the
        URLs actually delivered; raises NotifyError on the first hard failure so
        undelivered rooms are never marked seen."""
        sent: list[str] = []
        total = len(to_open)
        for i, s in enumerate(to_open, 1):
            self.send_text(format_listing(s, i, total), chat_id=chat_id)
            sent.append(s.listing.url)
            if i < total and self.delay_seconds > 0:
                time.sleep(self.delay_seconds)
        return sent
