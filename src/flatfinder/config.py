from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if getattr(sys, "frozen", False):
    # Packaged as a single executable (PyInstaller): keep config.yaml, .env and
    # data/ next to the .exe so they persist between runs and are user-editable.
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parents[2]

# ROOT holds the CODE + shipped assets (the package, config.example.yaml).
EXAMPLE_CONFIG_PATH = ROOT / "config.example.yaml"

# HOME holds the USER's stuff: config.yaml, .env and data/ (seen-DB, cache, logs).
# It defaults to ROOT so a plain in-folder install just works. Set FLATFINDER_HOME
# to a stable folder OUTSIDE the code (e.g. C:\Users\me\FlatfinderData) and your
# settings, API keys and seen-database survive across code edits, re-clones and new
# releases untouched — nothing to re-enter. See README ("Keep your settings…").
_home_env = os.environ.get("FLATFINDER_HOME", "").strip()
if _home_env:
    HOME = Path(_home_env).expanduser().resolve()
    HOME.mkdir(parents=True, exist_ok=True)
else:
    HOME = ROOT

DEFAULT_CONFIG_PATH = HOME / "config.yaml"


class OfficeConfig(BaseModel):
    # Example default = Charing Cross, central London. It is deliberately generic
    # (a public transport landmark, not anyone's real address) and fully functional
    # as a demo. Run `flatfinder setup` — or the first-run wizard — to point it at
    # your own workplace; that writes your postcode into config.yaml locally.
    name: str = "Central London (example)"
    address: str = "Charing Cross, London WC2N 5DU"
    postcode: str = "WC2N 5DU"
    lat: float = 51.508362
    lon: float = -0.124639


class BudgetConfig(BaseModel):
    max_pcm: float = 1450
    max_pw: float = 335
    # Floor on the NORMALISED monthly price. Listings priced per-week are always
    # converted to pcm before this compares, so a "£250 pw" (≈£1083 pcm) room is
    # never wrongly dropped by a raw weekly number. Only applied when the price
    # actually parsed — an unknown/None price is never rejected. Also filters out
    # mis-parsed junk prices (stray "£4 pcm" artefacts). 0 disables the floor.
    min_pcm: float = 900


class CommuteConfig(BaseModel):
    max_minutes: int = 30
    time_is: Literal["Arriving", "Departing"] = "Arriving"
    time: str = "09:00"
    date: str = "next_weekday"
    # Absolute crow-flies hard cap (km) — always skip TfL beyond this.
    prefilter_max_km: float = 22.0
    # Skip TfL if rough PT estimate > max_minutes * this (e.g. 1.35 → ~40 min).
    prefilter_estimate_slack: float = 1.35
    # Optional pre-emptive daily cap on TfL calls. None = off (recommended): the
    # app runs full speed and simply reports any real 429/rate-limit responses as
    # INCOMPLETE (TFL_LIMIT) so you're never blind. Set an int only if your plan
    # has a genuine daily quota you want to stop before.
    tfl_daily_limit: int | None = None


class SearchConfig(BaseModel):
    # Centre of the radius search. The wizard leaves this as "London"; the actual
    # radius is centred on your office coordinates, so this is just SpareRoom's
    # location seed and rarely needs changing.
    location: str = "London"
    max_pages: int = 30
    max_listings: int = 500
    # Safety valve for infrequent (e.g. monthly) users. After a long gap the
    # incremental walk can surface hundreds of new listings; detail fetches are
    # sequential + polite (~1.5s each), so processing them all would take many
    # minutes and hammer TfL. We fetch details for at most the NEWEST N per run
    # (older extras are skipped, not crashed on). Daily users never hit this since
    # they only have a few new listings. 0 = unlimited (old behaviour).
    max_details_per_run: int = 80
    # SpareRoom radius (miles). None → derived from commute.max_minutes.
    radius_miles: int | None = None
    # Newest-first: stop paginating once a page is entirely already-scanned.
    # Day-1 backfills to max_listings; later runs only walk new listings.
    incremental: bool = True
    search_url: str | None = None


class ScraperConfig(BaseModel):
    delay_seconds: float = 1.5
    use_proxy: bool = False
    max_retries: int = 3
    timeout_seconds: float = 30
    # Concurrency for OFFICIAL APIs only (TfL, postcodes.io, DeepSeek) — never
    # for SpareRoom detail fetches, which stay sequential/polite.
    api_concurrency: int = 12
    # Without a TfL key, TfL's rate limit is much lower, so firing 12 parallel
    # journeys would trigger 429 storms (especially on a big catch-up run). When
    # running keyless we cap TfL concurrency to this instead. Ignored once a key
    # is set. Journeys are cached, so this only affects the first pass over an area.
    keyless_tfl_concurrency: int = 4


class FilterConfig(BaseModel):
    require_location: bool = True
    double_only: bool = False
    bills_included_only: bool = False
    # Hard-drop flats with no (shared) living room, read from SpareRoom's structured
    # "Living room" detail field. FAIL-OPEN: only drops when that field explicitly
    # says "No"; missing/unparsed → kept, so a markup change can never hide rooms.
    # ON by default; flip off (config.yaml, the settings menu, or /livingroom off
    # in the Telegram chat) if you don't mind lounge-less flats.
    require_living_room: bool = True
    # Drop listings that are UNAMBIGUOUSLY short-term-only sublets: a structured
    # "Maximum term" at or under short_term_max_months, or explicit wording like
    # "short term only" / a "sublet" title. FAIL-OPEN like the living-room
    # filter — no max term / ambiguous wording ("short or long term") → kept.
    # Toggle from the Telegram chat with /shortterm on|off.
    exclude_short_term: bool = True
    short_term_max_months: int = 3


class PreferencesConfig(BaseModel):
    """Personal prefs. Move-in is soft by default — never hard-filters listings."""

    # Ideal move-in (ISO date), e.g. "2026-09-01". Used only for ranking badges /
    # a soft sort boost — it never hides listings. None = no move-in preference.
    ideal_move_in: str | None = None
    # If True (default), availability NEVER causes a listing to be dropped.
    move_in_soft_only: bool = True
    # Within this many days after ideal → "ok" badge; later → "late" (still shown).
    late_grace_days: int = 21


class AIConfig(BaseModel):
    """Optional AI quality filter (DeepSeek) for low-quality / scammy listings.

    OFF by default: the app is fully functional with no AI and no API key. Turn it
    on only if you add a DEEPSEEK_API_KEY — see the README. Everything else (search,
    commute filtering, dedupe, ranking, tabs) works exactly the same without it.
    """

    enabled: bool = False
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    min_score: int = 5  # keep if score >= this (1-10)
    max_to_scan: int = 25  # hard cap calls per run (also limited by $ budget)
    concurrency: int = 6  # parallel DeepSeek requests (budget still enforced)
    timeout_seconds: float = 60
    # ~$1/day soft ceiling (tracked in data/ai_budget.json)
    daily_budget_usd: float = 1.0
    # DeepSeek chat list prices (USD per 1M tokens) — update if they change
    # https://api-docs.deepseek.com/quick_start/pricing
    input_usd_per_million: float = 0.27
    output_usd_per_million: float = 1.10
    # Conservative estimate used to plan how many calls remain today
    est_cost_per_call_usd: float = 0.0015


class DailyConfig(BaseModel):
    """Once-a-day workflow: scrape → filter → open new tabs."""

    open_browser: bool = True
    max_tabs: int = 15
    tab_open_delay_seconds: float = 0.35
    mark_opened_as_seen: bool = True
    # Also mark AI-rejected / over-commute as seen? only opened ones by default
    show_ai_rejections: bool = True
    # Open flats with a shared living room first (then unknown, then explicit "no"),
    # so the first browser tabs are the ones with a lounge. Ordering only — nothing
    # is hidden. Lets you eyeball the living-room detection before hard-filtering.
    living_room_first: bool = True
    # Within that grouping, open listings whose availability is closest to
    # preferences.ideal_move_in first (secondary sort). Ordering only — never hides.
    move_in_first: bool = True


class AppConfig(BaseModel):
    office: OfficeConfig = Field(default_factory=OfficeConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    commute: CommuteConfig = Field(default_factory=CommuteConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    scraper: ScraperConfig = Field(default_factory=ScraperConfig)
    filter: FilterConfig = Field(default_factory=FilterConfig)
    preferences: PreferencesConfig = Field(default_factory=PreferencesConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    daily: DailyConfig = Field(default_factory=DailyConfig)
    db_path: str = "data/flatfinder.db"
    # Flipped to True once the first-run setup wizard completes (office pointed at a
    # real workplace). While False, launching offers to run setup. Nothing breaks if
    # you never do — the app just uses the Central London example office.
    configured: bool = False

    def resolved_radius_miles(self) -> int:
        """Search radius: explicit override, else derived from commute budget."""
        if self.search.radius_miles is not None:
            return self.search.radius_miles
        from flatfinder.geo.prefilter import radius_miles_for_minutes

        return radius_miles_for_minutes(
            self.commute.max_minutes,
            self.commute.prefilter_estimate_slack,
        )

    def resolved_db_path(self, root: Path | None = None) -> Path:
        base = root or HOME
        path = Path(self.db_path)
        if not path.is_absolute():
            path = base / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(HOME / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tfl_app_key: str = ""
    tfl_app_id: str = ""
    proxy_url: str = ""
    deepseek_api_key: str = ""
    # Vacation mode: `flatfinder daily --notify` sends new rooms to your phone
    # via a Telegram bot instead of opening browser tabs. See VACATION.md.
    # telegram_chat_id accepts a comma-separated list ("111,222"): every listed
    # chat gets its own settings + shortlist; the FIRST id is the admin.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


def bootstrap_config_file(path: Path | None = None) -> Path:
    """Ensure a writable config.yaml exists, seeding it from config.example.yaml.

    Called on launch so a fresh clone (which ships only config.example.yaml) gets a
    personal config.yaml that is git-ignored — your real office/budget never end up
    in a commit. Falls back to writing built-in defaults if the example is missing.
    """
    target = path or DEFAULT_CONFIG_PATH
    if target.exists():
        return target
    if EXAMPLE_CONFIG_PATH.exists():
        target.write_text(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        text = yaml.safe_dump(
            AppConfig().model_dump(mode="json"), default_flow_style=False, sort_keys=False
        )
        target.write_text(text, encoding="utf-8")
    return target


def load_config(path: Path | str | None = None) -> AppConfig:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if config_path.exists():
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return AppConfig.model_validate(raw)
    return AppConfig()


def load_env() -> EnvSettings:
    return EnvSettings()
