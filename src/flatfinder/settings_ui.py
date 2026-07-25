from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from flatfinder.config import (
    DEFAULT_CONFIG_PATH,
    AppConfig,
    ROOT,
    load_config,
    load_env,
)

console = Console()


def _sync_office_coords(config: AppConfig) -> None:
    """Geocode the office postcode → lat/lon so a changed postcode actually moves
    the commute destination. Fail-safe: keep existing coords if lookup fails."""
    from flatfinder.geo.postcodes import PostcodeClient

    if not config.office.postcode:
        return
    geo = PostcodeClient()
    try:
        coords = geo.geocode(config.office.postcode)
    finally:
        geo.close()
    if coords:
        config.office.lat, config.office.lon = coords
        console.print(
            f"[dim]Office → {config.office.postcode} "
            f"({config.office.lat:.4f}, {config.office.lon:.4f})[/dim]"
        )
    else:
        console.print(
            f"[yellow]Couldn't geocode '{config.office.postcode}' — keeping previous "
            "coordinates. Double-check the postcode.[/yellow]"
        )


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    path = path or DEFAULT_CONFIG_PATH
    data = config.model_dump(mode="json")
    # Keep YAML friendly nulls
    text = yaml.safe_dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    path.write_text(text, encoding="utf-8")
    return path


def show_config(config: AppConfig | None = None) -> None:
    config = config or load_config()
    env = load_env()

    table = Table(title="Current settings", box=box.ROUNDED, border_style="cyan")
    table.add_column("Key", style="bold dim")
    table.add_column("Value", style="white")

    rows = [
        ("office.name", config.office.name),
        ("office.postcode", config.office.postcode),
        ("budget.max_pcm", f"£{config.budget.max_pcm:.0f}"),
        ("budget.min_pcm", f"£{config.budget.min_pcm:.0f}" if config.budget.min_pcm else "—"),
        ("budget.max_pw", f"£{config.budget.max_pw:.0f}"),
        ("commute.max_minutes", config.commute.max_minutes),
        ("commute.time", f"{config.commute.time_is} {config.commute.time}"),
        ("commute.prefilter_max_km", config.commute.prefilter_max_km),
        ("preferences.ideal_move_in", config.preferences.ideal_move_in or "—"),
        ("preferences.move_in_soft_only", config.preferences.move_in_soft_only),
        ("preferences.late_grace_days", config.preferences.late_grace_days),
        ("search.max_pages", config.search.max_pages),
        ("search.max_listings", config.search.max_listings),
        ("filter.double_only", config.filter.double_only),
        ("filter.bills_included_only", config.filter.bills_included_only),
        ("filter.require_living_room", config.filter.require_living_room),
        ("filter.exclude_short_term", config.filter.exclude_short_term),
        ("filter.short_term_max_months", config.filter.short_term_max_months),
        ("daily.living_room_first", config.daily.living_room_first),
        ("daily.move_in_first", config.daily.move_in_first),
        ("ai.enabled", config.ai.enabled),
        ("ai.min_score", config.ai.min_score),
        ("ai.max_to_scan", config.ai.max_to_scan),
        ("ai.daily_budget_usd", f"${config.ai.daily_budget_usd:.2f}"),
        ("daily.max_tabs", config.daily.max_tabs),
        ("daily.open_browser", config.daily.open_browser),
        ("scraper.use_proxy", config.scraper.use_proxy),
        ("scraper.delay_seconds", config.scraper.delay_seconds),
        ("TFL_APP_KEY", "set" if env.tfl_app_key else "MISSING"),
        ("DEEPSEEK_API_KEY", "set" if env.deepseek_api_key else "not set"),
        ("config file", str(DEFAULT_CONFIG_PATH)),
    ]
    for k, v in rows:
        table.add_row(k, str(v))
    console.print(table)
    console.print(
        Panel(
            "[dim]Move-in date is [bold]soft[/bold]: listings always stay visible; "
            "we only re-rank / badge them. Negotiate availability on SpareRoom.[/dim]",
            border_style="dim",
        )
    )


# Fields the interactive editor can change (path → meta)
EDITABLE: list[dict[str, Any]] = [
    {"key": "budget.max_pcm", "label": "Max rent £/month", "type": "float", "hint": "e.g. 1450"},
    {"key": "budget.min_pcm", "label": "Min rent £/month (0 = no floor)", "type": "float", "hint": "e.g. 900 · weekly prices auto-converted"},
    {"key": "budget.max_pw", "label": "Max rent £/week (SpareRoom search)", "type": "float", "hint": "e.g. 335"},
    {"key": "commute.max_minutes", "label": "Max commute minutes", "type": "int", "hint": "e.g. 30"},
    {"key": "commute.time", "label": "Arrive-by time (HH:MM)", "type": "str", "hint": "09:00"},
    {"key": "commute.prefilter_max_km", "label": "Prefilter max km", "type": "float", "hint": "22"},
    {
        "key": "preferences.ideal_move_in",
        "label": "Ideal move-in date (YYYY-MM-DD)",
        "type": "str",
        "hint": "e.g. 2026-09-01 — soft only, type 'clear' to unset",
    },
    {
        "key": "preferences.late_grace_days",
        "label": "Days after ideal still 'ok' badge",
        "type": "int",
        "hint": "21",
    },
    {"key": "daily.max_tabs", "label": "Max browser tabs per daily run", "type": "int", "hint": "15"},
    {"key": "daily.open_browser", "label": "Open browser tabs", "type": "bool", "hint": "y/n"},
    {"key": "search.max_pages", "label": "Search pages to scrape", "type": "int", "hint": "15"},
    {"key": "search.max_listings", "label": "Max listings per run", "type": "int", "hint": "250"},
    {"key": "filter.double_only", "label": "Double rooms only", "type": "bool", "hint": "y/n"},
    {"key": "filter.bills_included_only", "label": "Bills included only", "type": "bool", "hint": "y/n"},
    {"key": "filter.require_living_room", "label": "Drop flats with NO living room", "type": "bool", "hint": "y/n · fail-open: unknown always kept"},
    {"key": "filter.exclude_short_term", "label": "Drop short-term-only sublets", "type": "bool", "hint": "y/n · fail-open: only unambiguous short lets"},
    {"key": "filter.short_term_max_months", "label": "Max term counted as short (months)", "type": "int", "hint": "3"},
    {"key": "daily.living_room_first", "label": "Open shared-living-room tabs first", "type": "bool", "hint": "y/n · ordering only, nothing hidden"},
    {"key": "daily.move_in_first", "label": "Open closest move-in dates first", "type": "bool", "hint": "y/n · secondary to living room"},
    {"key": "ai.enabled", "label": "DeepSeek AI filter", "type": "bool", "hint": "y/n"},
    {"key": "ai.min_score", "label": "AI min score to keep (1-10)", "type": "int", "hint": "5"},
    {"key": "ai.max_to_scan", "label": "Max AI scans per run", "type": "int", "hint": "25"},
    {"key": "ai.daily_budget_usd", "label": "AI daily $ budget", "type": "float", "hint": "1.0"},
    {"key": "scraper.use_proxy", "label": "Use residential proxy", "type": "bool", "hint": "y/n"},
    {"key": "scraper.delay_seconds", "label": "Scrape delay (seconds)", "type": "float", "hint": "1.5"},
    {"key": "office.postcode", "label": "Office postcode (auto-geocoded on save)", "type": "str", "hint": "e.g. EC2A 2AP"},
    {"key": "office.name", "label": "Office name / label", "type": "str", "hint": "e.g. Head office"},
]


def _get_path(cfg: AppConfig, dotted: str) -> Any:
    obj: Any = cfg
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _set_path(cfg: AppConfig, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    obj: Any = cfg
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def _prompt_value(meta: dict[str, Any], current: Any) -> Any | None:
    """Return new value or None if skipped."""
    label = meta["label"]
    t = meta["type"]
    hint = meta.get("hint", "")
    console.print(f"\n[bold]{label}[/bold]  [dim](now: {current!r} · {hint})[/dim]")
    if t == "bool":
        if Confirm.ask("Enable?", default=bool(current)):
            return True
        return False
    if t == "int":
        raw = Prompt.ask("New value (empty = keep)", default="")
        if raw.strip() == "":
            return None
        return int(raw)
    if t == "float":
        raw = Prompt.ask("New value (empty = keep)", default="")
        if raw.strip() == "":
            return None
        return float(raw)
    raw = Prompt.ask("New value (empty = keep)", default="")
    if raw.strip() == "":
        return None
    if meta["key"] == "preferences.ideal_move_in" and raw.strip().lower() in {"none", "clear", "-"}:
        return None
    return raw.strip()


def interactive_settings(path: Path | None = None) -> AppConfig:
    path = path or DEFAULT_CONFIG_PATH
    config = load_config(path)

    console.print(
        Panel.fit(
            "[bold]Settings editor[/bold]\n"
            "Move-in is [magenta]soft[/magenta]: never hides listings.\n"
            "Secrets (API keys) live in [cyan].env[/cyan] — edit that file manually.",
            border_style="magenta",
        )
    )

    while True:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
        table.add_column("#", width=3)
        table.add_column("Setting")
        table.add_column("Value")
        for i, meta in enumerate(EDITABLE, 1):
            val = _get_path(config, meta["key"])
            table.add_row(str(i), meta["label"], str(val))
        table.add_row("s", "Save & exit", "")
        table.add_row("q", "Quit without saving", "")
        console.print(table)

        choice = Prompt.ask("Edit #", default="s").strip().lower()
        if choice in {"q", "quit"}:
            console.print("[yellow]No changes saved.[/yellow]")
            return load_config(path)
        if choice in {"s", "save", ""}:
            # Enforce soft move-in
            config.preferences.move_in_soft_only = True
            _sync_office_coords(config)
            # Using the editor counts as configuring — stop offering first-run setup.
            config.configured = True
            out = save_config(config, path)
            console.print(f"[green]Saved[/green] → {out}")
            return config
        if not choice.isdigit() or not (1 <= int(choice) <= len(EDITABLE)):
            console.print("[red]Invalid choice[/red]")
            continue
        meta = EDITABLE[int(choice) - 1]
        current = _get_path(config, meta["key"])
        try:
            new = _prompt_value(meta, current)
        except (ValueError, TypeError) as e:
            console.print(f"[red]Bad value: {e}[/red]")
            continue
        if new is None and meta["type"] != "str":
            # empty keep — for ideal_move_in str, None means clear only via clear keyword
            if meta["key"] != "preferences.ideal_move_in":
                continue
        if meta["key"] == "preferences.ideal_move_in" and new is None and current:
            # user typed empty → keep; only clear keyword clears
            continue
        if new is not None or meta["key"] == "preferences.ideal_move_in":
            if meta["key"] == "preferences.ideal_move_in" and isinstance(new, str) and new.lower() in {"none", "clear"}:
                _set_path(config, meta["key"], None)
            elif new is not None:
                _set_path(config, meta["key"], new)
            console.print(f"[cyan]Updated {meta['key']} → {_get_path(config, meta['key'])!r}[/cyan]")
