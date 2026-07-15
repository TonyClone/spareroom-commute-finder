from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from flatfinder.config import HOME
from flatfinder.models import ScoredListing

LOG_DIR = HOME / "data" / "logs"
LATEST_JSON = LOG_DIR / "latest.json"
LATEST_TXT = LOG_DIR / "latest.txt"
APP_LOG = LOG_DIR / "flatfinder.log"


def ensure_log_dir() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


def setup_file_logging(verbose: bool = False) -> Path:
    """
    Attach a rotating file handler to the root logger (idempotent).
    Always logs INFO+ to data/logs/flatfinder.log so agents can review runs later.
    """
    ensure_log_dir()
    root = logging.getLogger()
    # Avoid duplicate handlers on re-entry
    for h in root.handlers:
        if getattr(h, "_flatfinder_file", False):
            if verbose:
                root.setLevel(logging.DEBUG)
            return APP_LOG

    level = logging.DEBUG if verbose else logging.INFO
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(
        APP_LOG,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    fh._flatfinder_file = True  # type: ignore[attr-defined]
    root.addHandler(fh)

    # Console only if nothing else is configured (CLI may already have basicConfig)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(level)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return APP_LOG


def _listing_brief(s: ScoredListing) -> dict[str, Any]:
    j = s.journey
    ai = s.ai
    return {
        "id": s.listing.id,
        "title": (s.listing.title or "")[:120],
        "url": s.listing.url,
        "price_pcm": s.listing.price_pcm,
        "postcode": s.listing.postcode,
        "area": s.listing.area,
        "lat": s.listing.lat,
        "lon": s.listing.lon,
        "transit_minutes": j.duration_minutes if j else None,
        "transfers": j.transfers if j else None,
        "journey_summary": (j.summary if j else "")[:200],
        "filter_pass": s.filter_pass,
        "fail_reason": s.fail_reason.value,
        "already_seen": s.already_seen,
        "available_from": s.listing.available_from,
        "available_date": s.available_date,
        "move_fit": s.move_fit,
        "move_note": s.move_note,
        "ai_keep": ai.keep if ai else None,
        "ai_score": ai.score if ai else None,
        "ai_summary": (ai.summary if ai else "")[:200],
        "ai_red_flags": (ai.red_flags if ai else [])[:5],
    }


def write_run_report(
    *,
    run_id: int,
    command: str,
    config_snapshot: dict[str, Any],
    scored: list[ScoredListing],
    opened_urls: list[str] | None = None,
    opened_ids: list[str] | None = None,
    ai_spend_note: str = "",
    extra: dict[str, Any] | None = None,
    progress_lines: list[str] | None = None,
) -> Path:
    """
    Write a structured report for one run:
      data/logs/run_{id}_{timestamp}.json
      data/logs/latest.json  (always overwritten)
      data/logs/latest.txt   (human summary)
    """
    ensure_log_dir()
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    from collections import Counter

    reasons = Counter(s.fail_reason.value for s in scored)
    passed = [s for s in scored if s.filter_pass]
    tfl_ok = sum(
        1
        for s in scored
        if s.journey and s.journey.status == "OK" and s.journey.duration_minutes is not None
    )
    tfl_err = sum(1 for s in scored if s.journey and s.journey.status == "ERROR")
    too_far = reasons.get("TOO_FAR", 0)
    over_commute = reasons.get("OVER_COMMUTE", 0)
    ai_rej = reasons.get("AI_REJECTED", 0)
    tfl_limit = reasons.get("TFL_LIMIT", 0)

    healthy = True
    health_notes: list[str] = []
    if not scored:
        healthy = False
        health_notes.append("No listings scraped/scored")
    if tfl_limit:
        healthy = False
        health_notes.append(
            f"INCOMPLETE: {tfl_limit} listing(s) NOT evaluated — TfL daily limit "
            f"reached. Re-run after the quota resets to finish."
        )
    if tfl_err and tfl_ok == 0 and any(
        s.fail_reason.value == "NO_JOURNEY" for s in scored
    ):
        healthy = False
        health_notes.append("All TfL journeys failed (check API path/key)")
    if command == "daily" and passed and not (opened_urls or opened_ids):
        # Not necessarily unhealthy (user may use --no-open)
        health_notes.append("Had passers but no tabs opened (check --no-open / dry-run)")

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": now.isoformat(),
        "run_id": run_id,
        "command": command,
        "healthy": healthy,
        "health_notes": health_notes,
        "config": config_snapshot,
        "counts": {
            "scored": len(scored),
            "filter_pass": len(passed),
            "tfl_ok": tfl_ok,
            "tfl_error": tfl_err,
            "too_far_prefilter": too_far,
            "over_commute": over_commute,
            "ai_rejected": ai_rej,
            "tfl_limit_unevaluated": tfl_limit,
            "opened_tabs": len(opened_urls or opened_ids or []),
        },
        "fail_reasons": dict(reasons),
        "ai_spend_note": ai_spend_note,
        "opened_ids": opened_ids or [],
        "opened_urls": opened_urls or [],
        "passed": [_listing_brief(s) for s in passed[:50]],
        "opened": [
            _listing_brief(s)
            for s in scored
            if s.listing.id in set(opened_ids or [])
            or s.listing.url in set(opened_urls or [])
        ][:30],
        "sample_rejects": {
            reason: [
                _listing_brief(s)
                for s in scored
                if s.fail_reason.value == reason
            ][:5]
            for reason in ("TOO_FAR", "OVER_COMMUTE", "OVER_BUDGET", "UNDER_BUDGET", "NO_LIVING_ROOM", "AI_REJECTED", "NO_JOURNEY", "NO_LOCATION")
            if any(s.fail_reason.value == reason for s in scored)
        },
        "progress_tail": (progress_lines or [])[-80:],
        "extra": extra or {},
        "log_file": str(APP_LOG),
    }

    path = LOG_DIR / f"run_{run_id}_{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    LATEST_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Human-readable snapshot
    lines = [
        f"Flatfinder run #{run_id} @ {now.isoformat()}",
        f"command={command} healthy={healthy}",
        f"scored={len(scored)} pass={len(passed)} opened={report['counts']['opened_tabs']}",
        f"fail_reasons={dict(reasons)}",
        f"tfl_ok={tfl_ok} tfl_error={tfl_err} too_far={too_far}",
        f"ai_spend={ai_spend_note or 'n/a'}",
    ]
    if health_notes:
        lines.append("notes: " + "; ".join(health_notes))
    lines.append("")
    lines.append("=== PASSED ===")
    for s in passed[:20]:
        j = s.journey
        mins = j.duration_minutes if j else "?"
        lines.append(
            f"  {mins}m £{s.listing.price_pcm} {s.listing.postcode or ''} | "
            f"{(s.listing.title or '')[:50]} | {s.listing.url}"
        )
    if opened_urls:
        lines.append("")
        lines.append("=== OPENED TABS ===")
        for u in opened_urls:
            lines.append(f"  {u}")
    lines.append("")
    lines.append(f"Full JSON: {path}")
    lines.append(f"App log:   {APP_LOG}")
    LATEST_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    logging.getLogger(__name__).info(
        "Run report written: %s (healthy=%s pass=%s opened=%s)",
        path.name,
        healthy,
        len(passed),
        report["counts"]["opened_tabs"],
    )
    return path
