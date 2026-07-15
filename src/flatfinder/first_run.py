"""First-run setup wizard.

The whole point of this file: someone non-technical double-clicks the launcher and,
without touching a config file or signing up for anything, ends up with the app
pointed at *their* office and *their* budget. Three questions, sensible defaults,
and everything is optional except the office location.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt

from flatfinder.config import (
    HOME,
    AppConfig,
    bootstrap_config_file,
    load_config,
)

console = Console()

TFL_SIGNUP_URL = "https://api-portal.tfl.gov.uk/"


def needs_setup(config: AppConfig) -> bool:
    """True until the user has completed setup (or edited settings) at least once."""
    return not config.configured


def _write_env_key(key: str, value: str, root: Path | None = None) -> None:
    """Create/update a single KEY=value line in .env without disturbing the rest."""
    env_path = (root or HOME) / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.strip().startswith(f"{key}=") or line.strip().startswith(f"# {key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def configure_tfl_key(console: Console | None = None, *, allow_skip: bool = True) -> bool:
    """Walk the user through getting a free TfL API key and save it to .env forever.

    Returns True if a key was saved. Used by the setup wizard and the menu, so the
    steps live in one place. Deliberately hand-holdy — the target user has never
    seen an API portal.
    """
    c = console or globals()["console"]
    c.print(
        Panel.fit(
            "[bold]Add a free TfL API key[/bold]  [dim](optional but recommended)[/dim]\n\n"
            "Flatfinder already works without one. A key just raises Transport for London's\n"
            "rate limit — worth it if you run big catch-up searches or don't run often.\n"
            "It's [bold]free[/bold] and takes about a minute. Nothing to install.",
            border_style="cyan",
        )
    )
    c.print(
        "  [bold]1.[/bold] We'll open [cyan]" + TFL_SIGNUP_URL + "[/cyan] in your browser.\n"
        "  [bold]2.[/bold] Click [bold]Sign up[/bold], enter your email, confirm it (free).\n"
        "  [bold]3.[/bold] Sign in → open [bold]Products[/bold] → subscribe to the free plan.\n"
        "  [bold]4.[/bold] Open [bold]Profile[/bold] → copy your [bold]Primary key[/bold].\n"
        "  [bold]5.[/bold] Paste it below. That's it — you won't need to do this again.\n"
    )
    if Confirm.ask("  Open the TfL sign-up page now?", default=True):
        try:
            webbrowser.open(TFL_SIGNUP_URL)
        except Exception:
            c.print(f"  [yellow]Couldn't open a browser — go to {TFL_SIGNUP_URL} manually.[/yellow]")

    prompt = "  Paste your TfL Primary key" + (" [dim](Enter to skip)[/dim]" if allow_skip else "")
    key = Prompt.ask(prompt, default="" if allow_skip else None).strip()
    if not key:
        if allow_skip:
            c.print("  [dim]Skipped — running keyless. Add one any time from the menu.[/dim]")
        return False
    _write_env_key("TFL_APP_KEY", key)
    c.print("  [green]✓ Saved.[/green] Your key is stored in .env and used automatically from now on.")
    return True


def _ask_office(config: AppConfig) -> None:
    from flatfinder.geo.postcodes import PostcodeClient

    console.print(
        "\n[bold]1/3 · Where do you work?[/bold]  "
        "[dim]We measure every room's real commute to here.[/dim]"
    )
    geo = PostcodeClient()
    try:
        while True:
            pc = Prompt.ask(
                "  Your work [bold]postcode[/bold] (e.g. EC2A 2AP)",
                default=config.office.postcode,
            ).strip()
            coords = geo.geocode(pc)
            if coords:
                config.office.postcode = pc.upper()
                config.office.lat, config.office.lon = coords
                console.print(f"  [green]✓[/green] Found it ({coords[0]:.4f}, {coords[1]:.4f}).")
                break
            console.print(
                "  [yellow]Hmm, couldn't find that postcode. Try again "
                "(UK postcodes only).[/yellow]"
            )
    finally:
        geo.close()
    label = Prompt.ask("  A name for it [dim](optional)[/dim]", default="Work").strip()
    config.office.name = label or "Work"
    config.office.address = f"{config.office.name}, {config.office.postcode}"


def run_setup_wizard(config_path: Path | str | None = None, *, force: bool = False) -> AppConfig:
    """Interactive setup. Returns the saved config."""
    from flatfinder.settings_ui import save_config

    path = Path(config_path) if config_path else bootstrap_config_file()
    config = load_config(path)

    if config.configured and not force:
        return config

    console.print(
        Panel.fit(
            "[bold cyan]Welcome to Flatfinder![/bold cyan]\n\n"
            "Let's set it up — takes about 30 seconds. You can change any of this later\n"
            "from the [bold]Settings[/bold] menu. No account or API key required.",
            border_style="cyan",
        )
    )

    _ask_office(config)

    console.print("\n[bold]2/3 · What's your budget?[/bold]")
    config.budget.max_pcm = float(
        IntPrompt.ask("  Max rent [bold]£ per month[/bold]", default=int(config.budget.max_pcm))
    )
    # Keep the SpareRoom weekly search cap roughly in sync with the monthly ceiling.
    config.budget.max_pw = round(config.budget.max_pcm * 12 / 52)

    console.print("\n[bold]3/3 · How far is too far?[/bold]")
    config.commute.max_minutes = IntPrompt.ask(
        "  Max [bold]door-to-door commute[/bold] in minutes", default=config.commute.max_minutes
    )

    # Optional key — the app works fine without it, so make skipping easy.
    console.print("\n[bold]One more thing (optional)[/bold]")
    if Confirm.ask("  Set up a free TfL API key now? [dim](improves reliability)[/dim]", default=False):
        configure_tfl_key(console, allow_skip=True)
    else:
        console.print("  [dim]No problem — running keyless. You can add one later from the menu.[/dim]")

    config.configured = True
    config.preferences.move_in_soft_only = True
    out = save_config(config, path)

    console.print(
        Panel.fit(
            f"[bold green]All set![/bold green]\n\n"
            f"Office:  [cyan]{config.office.name} ({config.office.postcode})[/cyan]\n"
            f"Budget:  [green]≤ £{config.budget.max_pcm:.0f}/month[/green]\n"
            f"Commute: [green]≤ {config.commute.max_minutes} min[/green] door-to-door\n\n"
            f"[dim]Saved to {out}. Starting your first hunt from the menu…[/dim]",
            border_style="green",
        )
    )
    return config
