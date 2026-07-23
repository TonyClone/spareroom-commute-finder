from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from flatfinder.ai.deepseek import DeepSeekFilter
from flatfinder.browser import open_tabs
from flatfinder.config import AppConfig, EnvSettings, load_config, load_env
from flatfinder.db import Database
from flatfinder.models import FailReason, ScoredListing
from flatfinder.notify import TelegramNotifier, format_header
from flatfinder.pipeline import run_pipeline
from flatfinder.rank import order_tabs, sort_scored
from flatfinder.runlog import setup_file_logging, write_run_report

logger = logging.getLogger(__name__)
ProgressFn = Callable[[str], None]


def _living_cell(living_room: str) -> str:
    """Render the shared-living-room state for the picks table."""
    lr = (living_room or "").lower()
    if lr == "no":
        return "[dim red]none[/dim red]"
    if lr == "":
        return "[dim]?[/dim]"
    return "[green]shared[/green]"


@dataclass
class DailyResult:
    run_id: int
    total_scraped: int = 0
    hard_pass: int = 0  # budget + commute
    unseen_pass: int = 0
    ai_kept: int = 0
    ai_rejected: int = 0
    opened: list[str] = field(default_factory=list)
    notified: list[str] = field(default_factory=list)
    to_open: list[ScoredListing] = field(default_factory=list)
    ai_rejected_items: list[ScoredListing] = field(default_factory=list)
    already_seen: int = 0
    fail_breakdown: dict[str, int] = field(default_factory=dict)


def run_daily(
    config: AppConfig | None = None,
    env: EnvSettings | None = None,
    *,
    open_browser: bool | None = None,
    use_ai: bool | None = None,
    max_tabs: int | None = None,
    dry_run: bool = False,
    notify: bool = False,
    console: Console | None = None,
) -> DailyResult:
    """
    Daily workflow:
      scrape → commute filter → dedupe seen → optional DeepSeek → open new tabs
      (or, with notify=True, send them to your phone via Telegram)
    """
    config = config or load_config()
    env = env or load_env()
    console = console or Console()
    setup_file_logging()
    db = Database(config.resolved_db_path())

    do_open = config.daily.open_browser if open_browser is None else open_browser
    do_ai = config.ai.enabled if use_ai is None else use_ai
    tabs_cap = max_tabs if max_tabs is not None else config.daily.max_tabs

    if notify and not (env.telegram_bot_token and env.telegram_chat_id):
        raise SystemExit(
            "--notify needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
            "(environment or .env) — see VACATION.md."
        )

    logger.info(
        "daily start office=%s max_pcm=%s max_min=%s ai=%s open=%s notify=%s dry_run=%s seen=%s",
        config.office.postcode,
        config.budget.max_pcm,
        config.commute.max_minutes,
        do_ai and bool(env.deepseek_api_key),
        do_open and not dry_run,
        notify and not dry_run,
        dry_run,
        db.seen_count(),
    )

    from flatfinder.display import print_banner, status_panel

    print_banner(console)
    console.print(status_panel(config, env, db))
    console.print(
        Panel.fit(
            f"AI filter: {'[green]on[/green]' if do_ai and env.deepseek_api_key else '[yellow]off[/yellow]'} · "
            f"Open tabs: {'[green]yes[/green]' if do_open and not dry_run else '[dim]no[/dim]'} · "
            f"Notify: {'[green]telegram[/green]' if notify and not dry_run else '[dim]off[/dim]'}"
            f"{' · [yellow]dry-run[/yellow]' if dry_run else ''}\n"
            f"Move-in ideal [magenta]{config.preferences.ideal_move_in or '—'}[/magenta] "
            f"[dim](soft — never hides listings)[/dim]",
            title="today's run",
            border_style="cyan",
        )
    )

    if not env.tfl_app_key:
        console.print(
            "[dim]No TFL_APP_KEY — using TfL keyless (lower rate limit, fine for a "
            "daily hunt since journeys are cached). Add a free key to .env only if "
            "you start seeing rate-limit warnings.[/dim]"
        )

    if do_ai and not env.deepseek_api_key:
        console.print(
            "[dim]AI filter is on but no DEEPSEEK_API_KEY is set — skipping it. "
            "Budget + commute filtering still apply (this is the normal, no-cost path).[/dim]"
        )
        do_ai = False

    log_lines: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("Scraping + commuting…", total=None)

        def on_progress(msg: str) -> None:
            log_lines.append(msg)
            logger.info(msg)
            # Keep description short for the bar
            short = msg if len(msg) < 80 else msg[:77] + "…"
            progress.update(task, description=short)

        run_id, scored = run_pipeline(config=config, env=env, progress=on_progress)

        progress.update(task, description="Deduping seen listings…")
        hard_pass = [s for s in scored if s.filter_pass]
        for s in hard_pass:
            if db.is_seen(s.listing.id):
                s.already_seen = True

        new_pass = [s for s in hard_pass if not s.already_seen]
        already_seen_n = len(hard_pass) - len(new_pass)

        ai_rejected: list[ScoredListing] = []
        if do_ai and new_pass:
            progress.update(task, description="DeepSeek quality scan (budget-capped)…")
            # Prefer shortest commute first so $1 is spent on the best candidates
            new_pass = sort_scored(new_pass)
            ds = DeepSeekFilter(api_key=env.deepseek_api_key, config=config.ai)
            try:
                scanned = ds.filter_batch(new_pass, progress=on_progress)
                ai_spend_note = ds.last_spend_note
            finally:
                ds.close()
            kept = [s for s in scanned if s.filter_pass]
            ai_rejected = [s for s in scanned if s.fail_reason == FailReason.AI_REJECTED]
            new_pass = kept
            by_id = {n.listing.id: n for n in scanned}
            for s in scored:
                n = by_id.get(s.listing.id)
                if n is None:
                    continue
                s.ai = n.ai
                s.filter_pass = n.filter_pass
                s.fail_reason = n.fail_reason
            db.replace_scores(run_id, scored)
            if ai_spend_note:
                on_progress(f"AI spend: {ai_spend_note}")
        else:
            ai_spend_note = ""

        progress.update(task, description="Ranking shortlist…")
        new_pass = sort_scored(new_pass)
        if config.daily.living_room_first or config.daily.move_in_first:
            # Ordering only (nothing hidden): open the best matches first (leftmost
            # tabs) — shared living room first, then availability closest to the
            # ideal move-in date. Selection of the top `tabs_cap` follows this order.
            new_pass = order_tabs(new_pass, config)
        to_open = new_pass[:tabs_cap]

        progress.update(task, description="Done", completed=1, total=1)

    result = DailyResult(
        run_id=run_id,
        total_scraped=len(scored),
        hard_pass=len(hard_pass),
        unseen_pass=len(hard_pass) - already_seen_n,
        ai_kept=len(new_pass),
        ai_rejected=len(ai_rejected),
        to_open=to_open,
        ai_rejected_items=ai_rejected,
        already_seen=already_seen_n,
        fail_breakdown=dict(Counter(s.fail_reason.value for s in scored)),
    )

    # --- UI tables ---
    summary = Table(title=f"Run #{run_id} summary", show_header=True)
    summary.add_column("Stage")
    summary.add_column("Count", justify="right")
    summary.add_row("Scraped", str(result.total_scraped))
    summary.add_row(f"Pass budget+commute (≤{config.commute.max_minutes}m, £{config.budget.max_pcm:.0f})", str(result.hard_pass))
    summary.add_row("Already seen (skipped)", str(result.already_seen))
    summary.add_row("New candidates", str(result.unseen_pass))
    if do_ai:
        summary.add_row("AI kept", str(result.ai_kept))
        summary.add_row("AI rejected (shitty)", str(result.ai_rejected))
        if ai_spend_note:
            summary.add_row("AI spend today", ai_spend_note)
    tfl_unevaluated = result.fail_breakdown.get("TFL_LIMIT", 0)
    if tfl_unevaluated:
        summary.add_row(
            "[red]⚠ Not evaluated (TfL daily limit)[/red]",
            f"[red]{tfl_unevaluated}[/red]",
        )
    summary.add_row("Tabs to open", str(len(to_open)))
    console.print(summary)

    if tfl_unevaluated:
        console.print(
            Panel.fit(
                f"[bold red]INCOMPLETE RUN[/bold red] — {tfl_unevaluated} listing(s) could not be "
                f"checked: the TfL daily limit was reached.\n"
                f"They're marked [bold]TFL_LIMIT[/bold] (not cached) and will be retried on your "
                f"next run once the quota resets.\n"
                f"[yellow]This shortlist is PARTIAL — you may be missing good matches.[/yellow]",
                title="⚠ TfL quota reached",
                border_style="red",
            )
        )

    if to_open:
        picks = Table(
            title="Opening these (new + relevant)",
            box=None,
            header_style="bold cyan",
            show_lines=False,
        )
        picks.add_column("#", justify="right")
        picks.add_column("Min", justify="right")
        picks.add_column("£", justify="right", style="green")
        picks.add_column("Move")
        picks.add_column("Living")
        picks.add_column("Avail")
        picks.add_column("AI", justify="right")
        picks.add_column("Where")
        picks.add_column("Title")
        for i, s in enumerate(to_open, 1):
            j = s.journey
            ai_s = str(s.ai.score) if s.ai else "—"
            picks.add_row(
                str(i),
                str(j.duration_minutes if j else "—"),
                f"{s.listing.price_pcm:.0f}" if s.listing.price_pcm else "—",
                s.move_fit or "?",
                _living_cell(s.listing.living_room),
                (s.listing.available_from or s.available_date or "—")[:14],
                ai_s,
                (s.listing.area or s.listing.postcode or "")[:14],
                (s.listing.title or "")[:40],
            )
        console.print(picks)
        console.print(
            "[dim]Move = soft badge only (flex/good/ok/late). Nothing hidden for dates.[/dim]"
        )
    else:
        console.print("[green]No new rooms to open today.[/green] You're caught up.")

    if config.daily.show_ai_rejections and ai_rejected:
        rej = Table(title="AI rejected (not opened)", style="dim")
        rej.add_column("Score")
        rej.add_column("Title")
        rej.add_column("Why")
        for s in ai_rejected[:15]:
            why = ""
            if s.ai:
                why = "; ".join(s.ai.red_flags or s.ai.reasons) or s.ai.summary
            rej.add_row(
                str(s.ai.score if s.ai else "?"),
                (s.listing.title or "")[:40],
                why[:60],
            )
        console.print(rej)

    # Mark AI rejects as seen so we don't re-scan / re-pay tokens tomorrow
    if ai_rejected and not dry_run:
        db.mark_seen(
            [s.listing.id for s in ai_rejected],
            opened=False,
            source="ai_reject",
        )

    # Send to phone (vacation mode): one Telegram message per room, so each
    # gets its own tappable preview card. Marked seen only after delivery —
    # a failed send re-surfaces those rooms on the next run instead of losing them.
    if notify and not dry_run:
        notifier = TelegramNotifier(env.telegram_bot_token, env.telegram_chat_id)
        try:
            notifier.send_text(
                format_header(
                    new_count=len(to_open),
                    total_scraped=result.total_scraped,
                    hard_pass=result.hard_pass,
                    already_seen=result.already_seen,
                    tfl_unchecked=tfl_unevaluated,
                ),
                preview=False,
                silent=not to_open,
            )
            if to_open:
                console.print(f"[cyan]Sending {len(to_open)} listing(s) to Telegram…[/cyan]")
                result.notified = notifier.send_shortlist(to_open)
        finally:
            notifier.close()
        if result.notified and config.daily.mark_opened_as_seen and not do_open:
            db.mark_seen([s.listing.id for s in to_open], opened=True, source="telegram")
            console.print(f"[dim]Marked {len(to_open)} listings as seen.[/dim]")
    elif notify and dry_run and to_open:
        console.print(f"[yellow]Dry-run: would send {len(to_open)} listing(s) to Telegram.[/yellow]")

    # Open browser
    if to_open and do_open and not dry_run:
        console.print(f"[cyan]Opening {len(to_open)} tab(s) in your browser…[/cyan]")
        urls = [s.listing.url for s in to_open]
        opened = open_tabs(
            urls,
            delay_seconds=config.daily.tab_open_delay_seconds,
            max_tabs=tabs_cap,
        )
        result.opened = opened
        if config.daily.mark_opened_as_seen:
            db.mark_seen([s.listing.id for s in to_open], opened=True, source="daily")
            console.print(f"[dim]Marked {len(to_open)} listings as seen.[/dim]")
    elif to_open and dry_run:
        console.print("[yellow]Dry-run: would open tabs, nothing marked seen.[/yellow]")
    elif to_open and not do_open and not notify:
        console.print(
            "[dim]Browser open disabled (--no-open). "
            "Listings were NOT marked seen so you can open them later.[/dim]"
        )

    # Durable report for next session / agent review
    report_path = write_run_report(
        run_id=run_id,
        command="daily",
        config_snapshot={
            "office": config.office.model_dump(),
            "budget": config.budget.model_dump(),
            "commute": config.commute.model_dump(),
            "search": config.search.model_dump(),
            "ai": config.ai.model_dump(),
            "daily": config.daily.model_dump(),
        },
        scored=scored,
        opened_urls=result.opened or result.notified,
        opened_ids=[s.listing.id for s in to_open] if (result.opened or result.notified) else [],
        ai_spend_note=ai_spend_note,
        progress_lines=log_lines,
        extra={
            "hard_pass": result.hard_pass,
            "unseen_pass": result.unseen_pass,
            "already_seen": result.already_seen,
            "ai_kept": result.ai_kept,
            "ai_rejected": result.ai_rejected,
            "dry_run": dry_run,
            "do_open": do_open,
            "notified": len(result.notified),
        },
    )
    logger.info("daily complete run_id=%s report=%s", run_id, report_path)

    console.print(
        Panel.fit(
            "[bold green]Done.[/bold green] Manually review the tabs, message what you like on SpareRoom.\n"
            "Next morning: run [bold]daily.ps1[/bold] or [bold]python -m flatfinder daily[/bold] again.\n"
            f"[dim]Logs: {report_path} · data/logs/latest.txt · data/logs/flatfinder.log[/dim]",
            border_style="green",
        )
    )
    return result
