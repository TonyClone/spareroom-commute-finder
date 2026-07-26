from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from flatfinder.config import HOME, ROOT, load_config, load_env
from flatfinder.db import Database

app = typer.Typer(
    help="[bold]Flatfinder[/bold] — find rooms by real door-to-door commute time, not map distance.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)
console = Console()


def _setup_logging(verbose: bool = False) -> None:
    from flatfinder.runlog import setup_file_logging

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    path = setup_file_logging(verbose=verbose)
    logging.getLogger(__name__).info("File logging → %s", path)


def _print_shortlist_table_clean(run_id: int, passed: list, *, limit: int = 50) -> None:
    table = Table(
        title=f"✦ Shortlist · run #{run_id} · {len(passed)} rooms",
        box=box.ROUNDED,
        border_style="green",
        header_style="bold cyan",
    )
    table.add_column("Min", justify="right", style="bold bright_white")
    table.add_column("Xfr", justify="right")
    table.add_column("£/mo", justify="right", style="green")
    table.add_column("Move", justify="center")
    table.add_column("Available")
    table.add_column("Where")
    table.add_column("Title")
    table.add_column("Link", style="dim")

    style_for = {
        "flexible": "bold cyan",
        "good": "bold green",
        "ok": "yellow",
        "late": "dim red",
        "unknown": "dim",
    }

    for s in passed[:limit]:
        j = s.journey
        fit = s.move_fit or "unknown"
        table.add_row(
            str(j.duration_minutes if j else "—"),
            str(j.transfers if j and j.transfers is not None else "—"),
            f"{s.listing.price_pcm:.0f}" if s.listing.price_pcm else "—",
            f"[{style_for.get(fit, 'dim')}]{fit}[/]",
            (s.listing.available_from or s.available_date or "—")[:16],
            (s.listing.area or s.listing.postcode or "")[:16],
            (s.listing.title or "")[:34],
            s.listing.url,
        )
    console.print(table)
    console.print(
        "[dim]Move badges are soft (flex/good/ok/late/?). Late listings are still shown — negotiate on SpareRoom.[/dim]"
    )


# ---------------------------------------------------------------------------
# Plain Python actions (safe to call from the interactive menu)
# ---------------------------------------------------------------------------


def do_show(config_path: Path | None = None) -> None:
    from flatfinder.display import print_banner, status_panel
    from flatfinder.settings_ui import show_config

    config = load_config(config_path)
    env = load_env()
    db = Database(config.resolved_db_path())
    print_banner(console)
    console.print(status_panel(config, env, db))
    console.print()
    show_config(config)


def do_settings(config_path: Path | None = None) -> None:
    from flatfinder.settings_ui import interactive_settings

    interactive_settings(config_path)


def do_tfl_key() -> None:
    from flatfinder.first_run import configure_tfl_key

    configure_tfl_key(console, allow_skip=True)


def do_update() -> None:
    from flatfinder import __version__
    from flatfinder.updater import update

    console.print(f"[dim]Flatfinder v{__version__} — checking for updates…[/dim]")
    update(progress=lambda m: console.print(f"  {m}"))


def do_daily(
    *,
    max_tabs: int | None = None,
    no_open: bool = False,
    no_ai: bool = False,
    dry_run: bool = False,
    notify: bool = False,
    max_pages: int | None = None,
    max_listings: int | None = None,
    config_path: Path | None = None,
    verbose: bool = False,
) -> None:
    from flatfinder.daily import run_daily

    _setup_logging(verbose)
    config = load_config(config_path)
    env = load_env()
    if max_pages is not None:
        config.search.max_pages = max_pages
    if max_listings is not None:
        config.search.max_listings = max_listings

    try:
        run_daily(
            config=config,
            env=env,
            open_browser=False if no_open else None,
            use_ai=False if no_ai else None,
            max_tabs=max_tabs,
            dry_run=dry_run,
            notify=notify,
            console=console,
        )
    except SystemExit as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e


def do_baseline(
    *,
    max_pages: int | None = None,
    max_listings: int | None = None,
    with_commute: bool = False,
    config_path: Path | None = None,
    verbose: bool = False,
) -> None:
    from flatfinder.pipeline import run_pipeline
    from flatfinder.scraper.http import HttpClient
    from flatfinder.scraper.spareroom import SpareRoomScraper

    _setup_logging(verbose)
    config = load_config(config_path)
    env = load_env()
    if max_pages is not None:
        config.search.max_pages = max_pages
    if max_listings is not None:
        config.search.max_listings = max_listings

    db = Database(config.resolved_db_path())
    console.print(
        Panel.fit(
            "[bold yellow]Baseline seed[/bold yellow]\n"
            "Mark current SpareRoom results as already seen.\n"
            "No browser tabs. Next [bold]daily[/bold] = only brand-new ads.",
            border_style="yellow",
        )
    )

    if with_commute:
        if not env.tfl_app_key:
            console.print(
                "[dim]No TFL_APP_KEY — running keyless (lower rate limit; fine for one pass).[/dim]"
            )
        console.print("Running full scrape + commute, then marking all scraped ids seen…")
        _run_id, scored = run_pipeline(
            config=config, env=env, progress=lambda m: console.print(f"  [dim]{m}[/dim]")
        )
        ids = [s.listing.id for s in scored]
        n = db.mark_seen(ids, opened=False, source="baseline")
        passed = sum(1 for s in scored if s.filter_pass)
        console.print(
            f"[green]Baseline done.[/green] Marked {n} seen "
            f"({passed} would pass commute/budget). Seen DB: {db.seen_count()}"
        )
        return

    http = HttpClient(
        proxy_url=env.proxy_url or None,
        use_proxy=config.scraper.use_proxy,
        delay_seconds=config.scraper.delay_seconds,
        timeout=config.scraper.timeout_seconds,
        max_retries=config.scraper.max_retries,
    )
    try:
        scraper = SpareRoomScraper(config, http)
        console.print("Scraping current listings (no TfL)…")
        listings = scraper.scrape(progress=lambda m: console.print(f"  [dim]{m}[/dim]"))
        for listing in listings:
            db.upsert_listing(listing)
        ids = [L.id for L in listings]
        n = db.mark_seen(ids, opened=False, source="baseline")
        console.print(
            f"[green]Baseline done.[/green] Scraped {len(ids)}, marked {n} seen. "
            f"Seen DB: {db.seen_count()}"
        )
    finally:
        http.close()


def do_smoke_tfl(from_postcode: str = "E2 8AA") -> None:
    from flatfinder.commute.tfl import TflJourneyClient
    from flatfinder.geo.postcodes import PostcodeClient

    env = load_env()
    config = load_config()
    if not env.tfl_app_key:
        console.print("[dim]No TFL_APP_KEY — testing keyless.[/dim]")
    console.print(f"[cyan]TfL[/cyan] {from_postcode} → {config.office.postcode} …")
    geo = PostcodeClient()
    coords = geo.geocode(from_postcode)
    geo.close()
    tfl = TflJourneyClient(app_key=env.tfl_app_key, app_id=env.tfl_app_id)
    try:
        j = tfl.journey(
            from_lat=coords[0] if coords else None,
            from_lon=coords[1] if coords else None,
            from_postcode=from_postcode if not coords else None,
            to_lat=config.office.lat,
            to_lon=config.office.lon,
            time_hhmm=config.commute.time,
            date_spec=config.commute.date,
            time_is=config.commute.time_is,
        )
        if j.status == "OK":
            console.print(
                Panel(
                    f"[bold green]{j.duration_minutes} min[/bold green]  ·  "
                    f"{j.transfers} transfers  ·  walk {j.walk_minutes}m\n"
                    f"[dim]{j.summary}[/dim]",
                    title="Journey OK",
                    border_style="green",
                )
            )
        else:
            console.print(Panel(f"[red]{j.status}[/red]\n{j.error}", border_style="red"))
    finally:
        tfl.close()


def do_export(
    out: Path = Path("data/shortlist.csv"),
    run_id: int | None = None,
    all_results: bool = False,
    config_path: Path | None = None,
) -> None:
    config = load_config(config_path)
    db = Database(config.resolved_db_path())
    rid = run_id or db.latest_run_id()
    if rid is None:
        console.print("[red]No runs found. Run daily first.[/red]")
        raise typer.Exit(1)
    rows = db.shortlist_for_run(rid, passed_only=not all_results)
    out = Path(out)
    if not out.is_absolute():
        out = HOME / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        console.print("[yellow]No rows to export.[/yellow]")
        raise typer.Exit(0)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    console.print(f"[green]Wrote {len(rows)} rows[/green] → {out}")


def do_last_run() -> None:
    from flatfinder.runlog import LATEST_JSON, LATEST_TXT

    if LATEST_TXT.exists():
        console.print(
            Panel(
                LATEST_TXT.read_text(encoding="utf-8"),
                title="latest.txt",
                border_style="cyan",
                box=box.ROUNDED,
            )
        )
    else:
        console.print("[yellow]No latest.txt yet — run daily once.[/yellow]")
    if LATEST_JSON.exists():
        import json

        data = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        healthy = data.get("healthy")
        style = "green" if healthy else "red"
        console.print(
            f"\n[{style}]healthy={healthy}[/{style}]  "
            f"run_id={data.get('run_id')}  "
            f"pass={data.get('counts', {}).get('filter_pass')}  "
            f"opened={data.get('counts', {}).get('opened_tabs')}"
        )
        console.print(f"[dim]{LATEST_JSON}[/dim]")


def do_run(
    *,
    max_minutes: int | None = None,
    max_pcm: float | None = None,
    max_pages: int | None = None,
    max_listings: int | None = None,
    config_path: Path | None = None,
    verbose: bool = False,
) -> None:
    from flatfinder.display import print_banner, status_panel
    from flatfinder.pipeline import run_pipeline
    from flatfinder.runlog import write_run_report

    _setup_logging(verbose)
    config = load_config(config_path)
    env = load_env()

    if not env.tfl_app_key:
        console.print(
            "[dim]No TFL_APP_KEY set — running keyless (lower rate limit). "
            "Add a free key to .env if you see rate-limit warnings.[/dim]"
        )

    if max_minutes is not None:
        config.commute.max_minutes = max_minutes
    if max_pcm is not None:
        config.budget.max_pcm = max_pcm
    if max_pages is not None:
        config.search.max_pages = max_pages
    if max_listings is not None:
        config.search.max_listings = max_listings

    print_banner(console)
    console.print(status_panel(config, env, Database(config.resolved_db_path())))
    console.print()

    run_id, scored = run_pipeline(
        config=config, env=env, progress=lambda m: console.print(f"  [dim]{m}[/dim]")
    )
    passed = [s for s in scored if s.filter_pass]

    report_path = write_run_report(
        run_id=run_id,
        command="run",
        config_snapshot={
            "office": config.office.model_dump(),
            "budget": config.budget.model_dump(),
            "commute": config.commute.model_dump(),
            "search": config.search.model_dump(),
            "preferences": config.preferences.model_dump(),
            "ai": config.ai.model_dump(),
        },
        scored=scored,
    )
    console.print(f"[dim]Report → {report_path}[/dim]\n")
    _print_shortlist_table_clean(run_id, passed)

    if not passed:
        from collections import Counter

        console.print("[yellow]No listings passed. Try raising max minutes or pages.[/yellow]")
        console.print("Fail reasons:", dict(Counter(s.fail_reason.value for s in scored)))


# ---------------------------------------------------------------------------
# Interactive menu (desktop launcher)
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Flatfinder — interactive menu when run with no subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    _setup_logging(verbose)
    _interactive_menu()


def _interactive_menu() -> None:
    """Full app UI for desktop launcher — never needs another window."""
    try:
        _menu_loop()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Bye — good luck with the hunt.[/dim]\n")
    except Exception:
        # A double-clicked launcher owns its console window: if startup crashes
        # (e.g. a hand-edited, broken config.yaml) the window would flash and
        # vanish before anyone can read the error. Show it and wait instead.
        import traceback

        from flatfinder.config import DEFAULT_CONFIG_PATH

        traceback.print_exc()
        print(
            "\nFlatfinder hit an unexpected error (details above).\n"
            f"The usual cause is a broken settings file — delete\n"
            f"  {DEFAULT_CONFIG_PATH}\n"
            "and launch again to redo the 3-question setup. If it keeps happening,\n"
            "report the text above at https://github.com/TonyClone/spareroom-commute-finder/issues"
        )
        try:
            input("\nPress Enter to close this window… ")
        except EOFError:
            pass
        raise SystemExit(1) from None


def _menu_loop() -> None:
    from flatfinder.config import bootstrap_config_file
    from flatfinder.display import print_home
    from flatfinder.first_run import needs_setup, refresh_desktop_shortcut, run_setup_wizard

    _setup_logging(False)
    # First launch on a fresh clone: get them set up before the menu appears.
    bootstrap_config_file()
    if needs_setup(load_config()):
        run_setup_wizard()
    else:
        # After a self-update the newest binary is a new file — repoint the
        # Desktop shortcut (if the user made one) so one double-click stays true.
        refresh_desktop_shortcut()
    while True:
        config = load_config()
        env = load_env()
        db = Database(config.resolved_db_path())
        try:
            console.clear()
        except Exception:
            console.print("\n" * 2)
        print_home(config, env, db)
        choice = Prompt.ask(
            "[bold cyan]Pick a number[/bold cyan]  [dim](Enter = daily hunt)[/dim]",
            choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "q"],
            default="1",
            show_choices=False,
        )
        if choice in {"0", "q"}:
            console.print("\n[dim]Bye — good luck with the hunt.[/dim]\n")
            return
        try:
            if choice == "1":
                do_daily()
            elif choice == "2":
                do_settings()
            elif choice == "3":
                do_show()
            elif choice == "4":
                do_last_run()
            elif choice == "5":
                do_baseline()
            elif choice == "6":
                do_smoke_tfl()
            elif choice == "7":
                do_export()
            elif choice == "8":
                do_tfl_key()
            elif choice == "9":
                do_update()
        except typer.Exit:
            pass
        except SystemExit as e:
            if e.code not in (0, None):
                console.print(f"[red]{e}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            logging.exception("menu action failed")
        Prompt.ask("\n[dim]Enter to return to menu[/dim]", default="")


@app.command()
def menu() -> None:
    """Open the interactive home menu (desktop launcher entrypoint)."""
    _interactive_menu()


@app.command()
def setup(
    config_path: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Run the first-time setup wizard (office, budget, commute)."""
    from flatfinder.first_run import run_setup_wizard

    run_setup_wizard(config_path, force=True)


@app.command()
def update() -> None:
    """Update to the latest release in place (ZIP installs). Git checkouts: use git pull."""
    do_update()


@app.command()
def show(config_path: Optional[Path] = typer.Option(None, "--config")) -> None:
    """Pretty-print current config & keys."""
    do_show(config_path)


@app.command("settings")
def settings(
    config_path: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Interactive editor for budget, commute, move-in, AI, tabs…"""
    do_settings(config_path)


@app.command()
def run(
    max_minutes: Optional[int] = typer.Option(None, help="Override max commute minutes"),
    max_pcm: Optional[float] = typer.Option(None, help="Override max £ per month"),
    max_pages: Optional[int] = typer.Option(None, help="Override search pages"),
    max_listings: Optional[int] = typer.Option(None, help="Override max listings"),
    config_path: Optional[Path] = typer.Option(None, "--config", help="Path to config.yaml"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Scrape SpareRoom, score with TfL, print shortlist (no browser)."""
    do_run(
        max_minutes=max_minutes,
        max_pcm=max_pcm,
        max_pages=max_pages,
        max_listings=max_listings,
        config_path=config_path,
        verbose=verbose,
    )


@app.command("export")
def export_csv(
    out: Path = typer.Argument(Path("data/shortlist.csv")),
    run_id: Optional[int] = typer.Option(None, help="Run id (default: latest)"),
    all_results: bool = typer.Option(False, "--all", help="Include failed listings"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Export shortlist for a run to CSV."""
    do_export(out=out, run_id=run_id, all_results=all_results, config_path=config_path)


@app.command()
def ui(
    config_path: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Launch Streamlit dashboard."""
    import subprocess
    import sys

    app_path = Path(__file__).parent / "ui" / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path), "--"]
    if config_path:
        cmd += ["--config", str(config_path)]
    raise SystemExit(subprocess.call(cmd, cwd=str(ROOT)))


@app.command("smoke-tfl")
def smoke_tfl(
    from_postcode: str = typer.Option("E2 8AA", help="Origin postcode to test"),
) -> None:
    """Quick TfL journey test to the office."""
    do_smoke_tfl(from_postcode)


@app.command()
def daily(
    max_tabs: Optional[int] = typer.Option(None, help="Max browser tabs to open"),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open browser tabs"),
    no_ai: bool = typer.Option(False, "--no-ai", help="Skip DeepSeek quality filter"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do everything except open/mark seen"),
    notify: bool = typer.Option(
        False,
        "--notify",
        help="Send new rooms to your phone via Telegram (vacation mode, see VACATION.md)",
    ),
    max_pages: Optional[int] = typer.Option(None, help="Override search pages"),
    max_listings: Optional[int] = typer.Option(None, help="Override max listings"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Once-a-day: scrape → commute → dedupe → optional AI → open new tabs."""
    do_daily(
        max_tabs=max_tabs,
        no_open=no_open,
        no_ai=no_ai,
        dry_run=dry_run,
        notify=notify,
        max_pages=max_pages,
        max_listings=max_listings,
        config_path=config_path,
        verbose=verbose,
    )


@app.command()
def baseline(
    max_pages: Optional[int] = typer.Option(None, help="Override search pages"),
    max_listings: Optional[int] = typer.Option(None, help="Override max listings"),
    with_commute: bool = typer.Option(
        False,
        "--with-commute",
        help="Also run TfL (slower). Default: mark every scraped id as seen, no tabs.",
    ),
    config_path: Optional[Path] = typer.Option(None, "--config"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Seed seen-DB with everything live now (only new ads open after)."""
    do_baseline(
        max_pages=max_pages,
        max_listings=max_listings,
        with_commute=with_commute,
        config_path=config_path,
        verbose=verbose,
    )


@app.command("last-run")
def last_run() -> None:
    """Show the latest run report (data/logs/latest.txt + health)."""
    do_last_run()


@app.command("seen-list")
def seen_list(
    config_path: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """How many listings are in the seen/dedup database."""
    config = load_config(config_path)
    db = Database(config.resolved_db_path())
    console.print(f"Seen listings: [bold]{db.seen_count()}[/bold]")


@app.command("seen-clear")
def seen_clear(
    yes: bool = typer.Option(False, "--yes", help="Confirm wipe"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Clear seen/dedup history (next daily will re-open old rooms)."""
    if not yes:
        console.print("[yellow]Pass --yes to wipe seen history.[/yellow]")
        raise typer.Exit(1)
    config = load_config(config_path)
    db = Database(config.resolved_db_path())
    n = db.clear_seen()
    console.print(f"Cleared [bold]{n}[/bold] seen listings.")


@app.command("mark-seen")
def mark_seen_cmd(
    listing_id: str = typer.Argument(..., help="SpareRoom flatshare_id"),
    config_path: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Manually mark a listing as already seen."""
    config = load_config(config_path)
    db = Database(config.resolved_db_path())
    db.mark_seen([listing_id], opened=False, source="manual")
    console.print(f"Marked [cyan]{listing_id}[/cyan] as seen.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
