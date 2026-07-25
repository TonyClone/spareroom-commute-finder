from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from flatfinder.ai.budget import DailyBudgetTracker, TokenUsage
from flatfinder.config import AIConfig
from flatfinder.models import AIVerdict, ScoredListing

logger = logging.getLogger(__name__)

# Keep prompt short → fewer input tokens (main cost lever).
SYSTEM_PROMPT = """London flatshare screener for a working professional.
Reject keep=false if: scam/WhatsApp-only/deposit-before-view; hot-bed/sofa-as-room;
8+ housemates in tight flat; curtain partition/not a real room; hostile landlord vibes;
obvious mould/no light marketed as fine; creepy/discriminatory ad.
Keep ordinary imperfect house-shares.
JSON only: {"keep":true,"score":1-10,"reasons":[],"red_flags":[],"summary":"one line"}"""


def _listing_blob(item: ScoredListing) -> str:
    listing = item.listing
    j = item.journey
    desc = (listing.description or "")[:500]
    lines = [
        f"{listing.title}",
        f"£{listing.price_pcm}/mo raw={listing.price_raw} | {listing.area} {listing.postcode or ''}",
        f"type={listing.room_type} bills={listing.bills_included} avail={listing.available_from}",
    ]
    if j and j.duration_minutes is not None:
        lines.append(f"TfL {j.duration_minutes}m xfer={j.transfers} | {j.summary[:120]}")
    lines.append(desc)
    return "\n".join(lines)


def _parse_verdict(text: str) -> AIVerdict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return AIVerdict(
                keep=True,
                score=5,
                error=f"bad JSON: {text[:200]}",
                summary="parse error — kept by default",
            )
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return AIVerdict(
                keep=True,
                score=5,
                error=f"bad JSON: {text[:200]}",
                summary="parse error — kept by default",
            )

    keep = bool(data.get("keep", True))
    try:
        score = int(data.get("score", 5))
    except (TypeError, ValueError):
        score = 5
    score = max(1, min(10, score))
    reasons = data.get("reasons") or []
    red_flags = data.get("red_flags") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    if not isinstance(red_flags, list):
        red_flags = [str(red_flags)]
    summary = str(data.get("summary") or "")
    return AIVerdict(
        keep=keep,
        score=score,
        reasons=[str(r) for r in reasons][:4],
        red_flags=[str(r) for r in red_flags][:4],
        summary=summary[:200],
    )


class DeepSeekFilter:
    def __init__(
        self,
        api_key: str,
        config: AIConfig | None = None,
        client: httpx.Client | None = None,
        budget: DailyBudgetTracker | None = None,
    ):
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required when AI filter is enabled")
        self.config = config or AIConfig()
        self.api_key = api_key
        self.budget = budget or DailyBudgetTracker(
            daily_budget_usd=self.config.daily_budget_usd,
            input_per_m=self.config.input_usd_per_million,
            output_per_m=self.config.output_usd_per_million,
        )
        self._client = client or httpx.Client(
            base_url=self.config.base_url.rstrip("/"),
            timeout=self.config.timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        self._owns = client is None
        self.last_spend_note = ""

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def evaluate(self, item: ScoredListing) -> AIVerdict:
        # Hard stop if daily budget exhausted
        if not self.budget.can_afford(self.config.est_cost_per_call_usd):
            return AIVerdict(
                keep=True,
                score=5,
                summary="skipped AI — daily budget cap reached",
                error="budget_cap",
            )

        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Screen:\n" + _listing_blob(item),
                },
            ],
            "temperature": 0.1,
            "max_tokens": 180,  # keep completions tiny
            "response_format": {"type": "json_object"},
        }
        try:
            r = self._client.post("/v1/chat/completions", json=payload)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            usage_raw = data.get("usage") or {}
            usage = TokenUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            )
            # If API omitted usage, use conservative estimate
            if usage.total_tokens == 0:
                usage = TokenUsage(prompt_tokens=450, completion_tokens=80)
            cost = self.budget.record(usage)
            logger.info(
                "DeepSeek %s: %s tokens ~$%.5f (%s)",
                item.listing.id,
                usage.total_tokens,
                cost,
                self.budget.summary(),
            )

            verdict = _parse_verdict(content)
            # A parse-failure verdict (error set) is a fail-open placeholder,
            # not a real score — demoting it below min_score would permanently
            # hide a listing just because the model emitted malformed JSON.
            if verdict.error:
                return verdict
            if verdict.keep and verdict.score < self.config.min_score:
                verdict.keep = False
                verdict.red_flags = list(verdict.red_flags) + [
                    f"score {verdict.score} < min {self.config.min_score}"
                ]
            return verdict
        except Exception as e:
            logger.warning("DeepSeek failed for %s: %s", item.listing.id, e)
            return AIVerdict(
                keep=True,
                score=5,
                error=str(e),
                summary="AI unavailable — kept by default",
            )

    def filter_batch(
        self,
        items: list[ScoredListing],
        *,
        progress: Any = None,
    ) -> list[ScoredListing]:
        """Attach AI verdicts in parallel; respect max_to_scan + daily $ budget.

        The number of calls is bounded up front (``limit``) so parallelism can't
        overspend; the thread-safe budget tracker is the backstop. Items beyond
        the cap are marked not-scanned. Listings are mutated in place.
        """
        from flatfinder.models import FailReason

        hard_cap = min(len(items), self.config.max_to_scan)
        est = max(self.config.est_cost_per_call_usd, 1e-6)
        budget_calls = int(self.budget.remaining() / est)
        limit = min(hard_cap, max(0, budget_calls))

        to_scan = items[:limit]
        for item in items[limit:]:
            item.ai = AIVerdict(
                keep=True,
                score=5,
                summary="not scanned (budget or max_to_scan cap)",
                error="cap",
            )

        workers = max(1, min(self.config.concurrency, len(to_scan)))
        if progress:
            progress(
                f"DeepSeek budget: {self.budget.summary()} → scanning "
                f"{len(to_scan)}/{len(items)} (parallel ×{workers})"
            )

        stopped_budget = limit < len(items)
        done = 0
        if to_scan:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for item, verdict in zip(to_scan, ex.map(self.evaluate, to_scan)):
                    item.ai = verdict
                    if verdict.error == "budget_cap":
                        stopped_budget = True
                    elif not verdict.keep:
                        item.filter_pass = False
                        item.fail_reason = FailReason.AI_REJECTED
                    done += 1
                    if progress and done % 5 == 0:
                        progress(f"DeepSeek {done}/{len(to_scan)} ({self.budget.summary()})")

        self.last_spend_note = self.budget.summary()
        if stopped_budget and progress:
            progress(f"DeepSeek stopped at cap — {self.last_spend_note}")
        return items
