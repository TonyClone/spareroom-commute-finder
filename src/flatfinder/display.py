from __future__ import annotations

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from flatfinder.config import AppConfig, EnvSettings
from flatfinder.db import Database

console = Console()

BANNER = r"""
[bold cyan]
  ███████╗██╗      █████╗ ████████╗
  ██╔════╝██║     ██╔══██╗╚══██╔══╝
  █████╗  ██║     ███████║   ██║   
  ██╔══╝  ██║     ██╔══██║   ██║   
  ██║     ███████╗██║  ██║   ██║   
  ╚═╝     ╚══════╝╚═╝  ╚═╝   ╚═╝   [/bold cyan][bold white]finder[/bold white]
"""


def print_banner(console: Console | None = None) -> None:
    c = console or globals()["console"]
    c.print(Align.center(Text.from_markup(BANNER)))
    c.print(
        Align.center(
            "[dim]SpareRoom · TfL door-to-door · optional DeepSeek · personal use[/dim]"
        )
    )
    from flatfinder.updater import build_label

    label, is_dev = build_label()
    badge = (
        f"[bold yellow]DEV[/bold yellow] [yellow]{label}[/yellow] [dim](unreleased local build)[/dim]"
        if is_dev
        else f"[green]release {label}[/green]"
    )
    c.print(Align.center(badge))
    c.print()


def status_panel(config: AppConfig, env: EnvSettings, db: Database | None = None) -> Panel:
    seen = db.seen_count() if db else "—"
    move = config.preferences.ideal_move_in or "not set"
    soft = "soft only (never hide)" if config.preferences.move_in_soft_only else "STRICT"
    keys = []
    keys.append("[green]TfL✓ key[/green]" if env.tfl_app_key else "[cyan]TfL keyless[/cyan]")
    keys.append("[green]AI✓[/green]" if env.deepseek_api_key else "[dim]AI off[/dim]")
    if config.scraper.use_proxy:
        keys.append("[cyan]proxy[/cyan]")

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold dim", justify="right")
    grid.add_column()
    from flatfinder.updater import build_label

    vlabel, vdev = build_label()
    grid.add_row(
        "Build",
        f"[yellow]{vlabel}  (dev — unreleased)[/yellow]" if vdev else f"[green]{vlabel}  (release)[/green]",
    )
    grid.add_row("Office", f"{config.office.name}  [cyan]{config.office.postcode}[/cyan]")
    if config.budget.min_pcm:
        budget_txt = (
            f"[green]£{config.budget.min_pcm:.0f}[/green]–[green]£{config.budget.max_pcm:.0f}[/green] / month"
        )
    else:
        budget_txt = f"≤ [green]£{config.budget.max_pcm:.0f}[/green] / month"
    grid.add_row("Budget", budget_txt)
    grid.add_row(
        "Commute",
        f"≤ [green]{config.commute.max_minutes} min[/green] PT · arrive {config.commute.time}",
    )
    if config.filter.require_living_room:
        grid.add_row("Living room", "[green]shared required[/green]  [dim](no-lounge dropped)[/dim]")
    elif config.daily.living_room_first:
        grid.add_row("Living room", "[cyan]shared opens first[/cyan]  [dim](nothing hidden)[/dim]")
    if config.filter.exclude_short_term:
        grid.add_row(
            "Short lets",
            f"[green]dropped[/green]  [dim](max term ≤ {config.filter.short_term_max_months}mo "
            "or 'short term only')[/dim]",
        )
    move_note = soft
    if config.daily.move_in_first and config.preferences.ideal_move_in:
        move_note = f"{soft} · closest opens first"
    grid.add_row("Move-in", f"[magenta]{move}[/magenta]  [dim]({move_note})[/dim]")
    grid.add_row("Daily tabs", str(config.daily.max_tabs))
    grid.add_row("AI", f"{'on' if config.ai.enabled else 'off'} · ${config.ai.daily_budget_usd:.2f}/day cap")
    grid.add_row("Seen DB", str(seen))
    grid.add_row("Keys", "  ".join(keys))

    return Panel(
        grid,
        title="[bold]Your hunt[/bold]",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 2),
    )


def menu_table() -> Table:
    t = Table(
        title="What do you want to do?",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    t.add_column("#", style="bold bright_cyan", width=3)
    t.add_column("Action", style="bold white")
    t.add_column("What it does", style="dim")
    t.add_row("1", "Daily hunt", "Scrape → commute → AI → open new tabs  [Enter]")
    t.add_row("2", "Settings", "Edit budget, commute, move-in date, AI, tabs…")
    t.add_row("3", "Show config", "View everything currently configured")
    t.add_row("4", "Last run", "Health check + what opened last time")
    t.add_row("5", "Baseline", "Mark all live ads as seen (only NEW after this)")
    t.add_row("6", "Test TfL", "Smoke-test journey times to the office")
    t.add_row("7", "Export CSV", "Save latest shortlist to data/shortlist.csv")
    t.add_row("8", "TfL key", "Add / update your free TfL API key (higher limits)")
    t.add_row("9", "Update", "Get the latest version (no re-download needed)")
    t.add_row("0", "Quit", "Close Flatfinder")
    return t


def print_home(config: AppConfig, env: EnvSettings, db: Database | None = None) -> None:
    print_banner()
    console.print(status_panel(config, env, db))
    console.print()
    console.print(menu_table())
    console.print(
        Align.center(
            "[dim]Everything runs in this window — no other apps needed.[/dim]"
        )
    )
    console.print()
