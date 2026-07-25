"""Per-user settings from the Telegram chat — tappable menu card + commands.

The vacation workflow runs headless every couple of hours, so there's no
terminal to open the settings editor in. Instead, the bot chat doubles as the
console. Send /menu (or /settings, /start) and you get a **menu card** — an
inline keyboard where every button tap toggles a filter or steps a number.
Typed commands (`/set budget.max_pcm 1400`, `/livingroom on`) still work.

Because the bot only wakes when a run starts, taps and commands QUEUE and are
applied at the start of the NEXT run: the run answers each tap, applies the
change, refreshes the menu card in place, and hunts with the new settings.

Settings are PER USER, not global:
  * `TELEGRAM_CHAT_ID` may hold several comma-separated chat ids. The first is
    the PRIMARY (admin). Every listed chat gets its own personal settings and
    its own filtered shortlist each run. Messages from any chat NOT on the
    list are consumed silently — never applied, never answered.
  * PERSONAL settings (budget, commute minutes, filters, move-in, rooms per
    run) apply only to the chat that set them.
  * GLOBAL settings (commute time, search size, the AI filter) shape the one
    shared scrape and can only be changed from the primary chat.
  * The scrape itself runs on an ENVELOPE config — the most permissive union
    of every user's personal settings — so one search covers everyone, and
    each user's shortlist is then re-filtered from it.

Overrides are persisted per chat in the app_state table (`telegram_config_
overrides:<chat_id>`), which rides the encrypted runner-state branch between
cloud runs. They layer on top of config.yaml until /unset (or the menu's
Reset) removes them. config.yaml itself is never written.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from flatfinder.config import AppConfig, EnvSettings
from flatfinder.db import Database
from flatfinder.notify import NotifyError, TelegramNotifier
from flatfinder.prices import pcm_to_pw

logger = logging.getLogger(__name__)

# app_state keys
STATE_OVERRIDES_PREFIX = "telegram_config_overrides"  # + ":<chat_id>" per user
STATE_LEGACY_OVERRIDES = "telegram_config_overrides"  # pre-per-user single blob
STATE_LAST_UPDATE_ID = "telegram_last_update_id"

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

_TRUE_WORDS = {"on", "true", "yes", "y", "1", "enable", "enabled"}
_FALSE_WORDS = {"off", "false", "no", "n", "0", "disable", "disabled"}

SCOPE_PERSONAL = "personal"
SCOPE_GLOBAL = "global"


@dataclass(frozen=True)
class Setting:
    key: str  # dotted config path
    type: str  # bool | int | float | time | date
    label: str  # short human name (also the menu button label)
    emoji: str = "⚙️"
    min: float | None = None
    max: float | None = None
    step: float = 0  # menu stepper: small increment
    big: float = 0  # menu stepper: large increment
    choices: tuple[str, ...] = ()  # menu presets (time type)
    clearable: bool = False  # value may be unset with "clear"/"none"
    scope: str = SCOPE_PERSONAL  # personal (per chat) | global (primary only)


# Every key the chat may change. Deliberately a whitelist: office location and
# scraper politeness knobs stay out of reach of a lost phone.
SETTABLE: list[Setting] = [
    # ---- personal: apply only to the chat that sets them ----
    Setting("budget.max_pcm", "float", "Max rent £/mo", "💰", min=100, max=20_000, step=50, big=100),
    Setting("budget.min_pcm", "float", "Min rent £/mo", "🪙", min=0, max=20_000, step=50, big=100),
    Setting("commute.max_minutes", "int", "Max commute min", "🚇", min=5, max=180, step=5, big=10),
    Setting("filter.require_living_room", "bool", "No-lounge filter", "🛋"),
    Setting("filter.exclude_short_term", "bool", "Short-let filter", "📆"),
    Setting("filter.short_term_max_months", "int", "Short = ≤ months", "📏", min=1, max=12, step=1, big=3),
    Setting("filter.double_only", "bool", "Doubles only", "🛏"),
    Setting("filter.bills_included_only", "bool", "Bills included only", "🧾"),
    Setting("preferences.ideal_move_in", "date", "Ideal move-in", "📅", clearable=True),
    Setting("preferences.late_grace_days", "int", "Late-grace days", "⏳", min=0, max=365, step=7, big=14),
    Setting("daily.max_tabs", "int", "Rooms per run", "📨", min=1, max=50, step=1, big=5),
    Setting("daily.living_room_first", "bool", "Lounge rooms first", "🥇"),
    Setting("daily.move_in_first", "bool", "Closest move-in first", "🗓"),
    # ---- global: shape the one shared scrape — primary chat only ----
    Setting("commute.time", "time", "Arrive-by time", "⏰", choices=("08:00", "08:30", "09:00", "09:30", "10:00"), scope=SCOPE_GLOBAL),
    Setting("search.max_pages", "int", "Search pages", "🔎", min=1, max=100, step=5, big=10, scope=SCOPE_GLOBAL),
    Setting("search.max_listings", "int", "Max listings", "📚", min=20, max=2_000, step=50, big=100, scope=SCOPE_GLOBAL),
    Setting("ai.enabled", "bool", "AI quality filter", "🤖", scope=SCOPE_GLOBAL),
    Setting("ai.min_score", "int", "AI min score", "🎯", min=1, max=10, step=1, big=2, scope=SCOPE_GLOBAL),
]

SETTINGS_BY_KEY: dict[str, Setting] = {s.key: s for s in SETTABLE}
PERSONAL_KEYS = [s.key for s in SETTABLE if s.scope == SCOPE_PERSONAL]
GLOBAL_KEYS = [s.key for s in SETTABLE if s.scope == SCOPE_GLOBAL]

# Friendly shortcuts: `/livingroom on` == `/set filter.require_living_room on`
ALIASES: dict[str, str] = {
    "budget": "budget.max_pcm",
    "minbudget": "budget.min_pcm",
    "commute": "commute.max_minutes",
    "livingroom": "filter.require_living_room",
    "living_room": "filter.require_living_room",
    "lounge": "filter.require_living_room",
    "shortterm": "filter.exclude_short_term",
    "short_term": "filter.exclude_short_term",
    "sublets": "filter.exclude_short_term",
    "double": "filter.double_only",
    "bills": "filter.bills_included_only",
    "movein": "preferences.ideal_move_in",
    "move_in": "preferences.ideal_move_in",
    "tabs": "daily.max_tabs",
    "rooms": "daily.max_tabs",
    "ai": "ai.enabled",
}


def allowed_chat_ids(env: EnvSettings) -> list[str]:
    """Chats the bot listens to, primary (admin) first.

    TELEGRAM_CHAT_ID accepts a comma-separated list ("111,222") so a partner
    can hunt with their own settings; a single id keeps working unchanged."""
    raw = (env.telegram_chat_id or "").replace(";", ",")
    ids = [c.strip() for c in raw.split(",") if c.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for c in ids:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def resolve_key(raw: str) -> Setting | None:
    k = raw.strip().lower().lstrip("/")
    k = ALIASES.get(k, k)
    return SETTINGS_BY_KEY.get(k)


def coerce_value(setting: Setting, raw: str) -> Any:
    """Parse + range-check a chat-typed value. Raises ValueError with a
    human-readable message (sent straight back as the bot reply)."""
    raw = raw.strip()
    if setting.clearable and raw.lower() in {"clear", "none", "-", "unset"}:
        return None
    if setting.type == "bool":
        low = raw.lower()
        if low in _TRUE_WORDS:
            return True
        if low in _FALSE_WORDS:
            return False
        raise ValueError(f"'{raw}' isn't on/off")
    if setting.type in {"int", "float"}:
        try:
            num = float(raw.replace("£", "").replace(",", ""))
        except ValueError:
            raise ValueError(f"'{raw}' isn't a number") from None
        if setting.min is not None and num < setting.min:
            raise ValueError(f"{num:g} is below the minimum ({setting.min:g})")
        if setting.max is not None and num > setting.max:
            raise ValueError(f"{num:g} is above the maximum ({setting.max:g})")
        return int(num) if setting.type == "int" else num
    if setting.type == "time":
        if not _TIME_RE.match(raw):
            raise ValueError(f"'{raw}' isn't HH:MM")
        hh, mm = raw.split(":")
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ValueError(f"'{raw}' isn't a valid time")
        return f"{int(hh):02d}:{mm}"
    if setting.type == "date":
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            raise ValueError(f"'{raw}' isn't YYYY-MM-DD (or 'clear')") from None
    return raw


def _fmt(value: Any) -> str:
    if value is True:
        return "on"
    if value is False:
        return "off"
    if value is None:
        return "—"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _get(config: AppConfig, dotted: str) -> Any:
    obj: Any = config
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


# ---------------------------------------------------------------------------
# Persistence (per chat)
# ---------------------------------------------------------------------------


def _overrides_key(chat_id: str) -> str:
    return f"{STATE_OVERRIDES_PREFIX}:{chat_id}"


def load_overrides(db: Database, chat_id: str) -> dict[str, Any]:
    raw = db.get_state(_overrides_key(chat_id))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Corrupt overrides state for chat %s — ignoring", chat_id)
        return {}
    return data if isinstance(data, dict) else {}


def save_overrides(db: Database, chat_id: str, overrides: dict[str, Any]) -> None:
    db.set_state(_overrides_key(chat_id), json.dumps(overrides))


def _migrate_legacy_overrides(db: Database, primary: str) -> None:
    """Move the pre-per-user single override blob onto the primary chat."""
    raw = db.get_state(STATE_LEGACY_OVERRIDES)
    if not raw:
        return
    if not db.get_state(_overrides_key(primary)):
        db.set_state(_overrides_key(primary), raw)
        logger.info("Migrated legacy chat overrides to primary chat %s", primary)
    db.set_state(STATE_LEGACY_OVERRIDES, "")


def apply_overrides(
    config: AppConfig, overrides: dict[str, Any], *, scopes: tuple[str, ...] = (SCOPE_PERSONAL, SCOPE_GLOBAL)
) -> list[str]:
    """Set stored overrides onto the loaded config (in place).

    Returns human-readable notes of what was applied. Unknown/stale keys are
    skipped with a warning, never fatal — a config schema change must not be
    able to kill the hunt."""
    applied: list[str] = []
    for key, value in overrides.items():
        setting = SETTINGS_BY_KEY.get(key)
        if setting is None:
            logger.warning("Skipping unknown settings override %r", key)
            continue
        if setting.scope not in scopes:
            continue
        parts = key.split(".")
        obj: Any = config
        try:
            for part in parts[:-1]:
                obj = getattr(obj, part)
            setattr(obj, parts[-1], value)
        except (AttributeError, TypeError, ValueError) as e:
            logger.warning("Could not apply override %s=%r: %s", key, value, e)
            continue
        applied.append(f"{key}={_fmt(value)}")
    return applied


# ---------------------------------------------------------------------------
# Per-user effective configs + the shared scrape envelope
# ---------------------------------------------------------------------------


@dataclass
class RemoteSettings:
    """Everything a run needs to honour per-user chat settings."""

    chats: list[str] = field(default_factory=list)  # allowed ids, primary first
    primary: str | None = None
    per_chat: dict[str, AppConfig] = field(default_factory=dict)  # effective per user
    envelope: AppConfig | None = None  # permissive union → drives the scrape

    def primary_config(self, fallback: AppConfig) -> AppConfig:
        if self.primary and self.primary in self.per_chat:
            return self.per_chat[self.primary]
        return fallback


def build_effective_configs(
    base: AppConfig, chats: list[str], overrides_by_chat: dict[str, dict[str, Any]]
) -> RemoteSettings:
    """base config.yaml + primary's GLOBAL overrides + each chat's PERSONAL ones.

    The envelope is the most permissive union across users on every personal
    axis, so the single scrape/TfL pass covers everyone; each user's shortlist
    is then re-filtered from it with their own config."""
    primary = chats[0] if chats else None
    shared = base.model_copy(deep=True)
    if primary is not None:
        # Global knobs (search size, commute time, AI) come from the primary.
        apply_overrides(shared, overrides_by_chat.get(primary, {}), scopes=(SCOPE_GLOBAL,))

    per_chat: dict[str, AppConfig] = {}
    for chat in chats:
        cfg = shared.model_copy(deep=True)
        apply_overrides(cfg, overrides_by_chat.get(chat, {}), scopes=(SCOPE_PERSONAL,))
        per_chat[chat] = cfg

    envelope = shared.model_copy(deep=True)
    users = list(per_chat.values()) or [shared]
    envelope.budget.max_pcm = max(u.budget.max_pcm for u in users)
    envelope.budget.min_pcm = min(u.budget.min_pcm for u in users)
    envelope.commute.max_minutes = max(u.commute.max_minutes for u in users)
    # Post-TfL filters only drop at the envelope stage when EVERY user filters.
    envelope.filter.require_living_room = all(u.filter.require_living_room for u in users)
    envelope.filter.exclude_short_term = all(u.filter.exclude_short_term for u in users)
    envelope.filter.short_term_max_months = min(u.filter.short_term_max_months for u in users)
    envelope.filter.double_only = all(u.filter.double_only for u in users)
    envelope.filter.bills_included_only = all(u.filter.bills_included_only for u in users)
    # SpareRoom's weekly search cap must admit the biggest budget in play.
    envelope.budget.max_pw = max(
        envelope.budget.max_pw, float(int(pcm_to_pw(envelope.budget.max_pcm)) + 1)
    )
    return RemoteSettings(chats=chats, primary=primary, per_chat=per_chat, envelope=envelope)


# ---------------------------------------------------------------------------
# Menu card (inline keyboard)
# ---------------------------------------------------------------------------
# Callback data grammar (Telegram caps it at 64 bytes):
#   t:<key>            toggle a bool
#   m:<key>            open the stepper/preset submenu for a key
#   a:<key>:<delta>    adjust a numeric key by +/- delta
#   v:<key>:<value>    set an explicit value (time presets)
#   w:<key>:<days>     shift a date key by +/- days (from today if unset)
#   u:<key>            reset the key (drop the chat override)
#   r                  back to the root menu


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _row_label(setting: Setting, config: AppConfig, overrides: dict[str, Any]) -> str:
    star = " ★" if setting.key in overrides else ""
    return f"{setting.emoji} {setting.label}: {_fmt(_get(config, setting.key))}{star}"


def build_menu(
    config: AppConfig, overrides: dict[str, Any], *, is_primary: bool
) -> tuple[str, dict]:
    """(text, inline keyboard) for the root settings card of ONE chat."""
    rows: list[list[dict]] = []
    for s in SETTABLE:
        if s.scope == SCOPE_GLOBAL and not is_primary:
            continue
        data = f"t:{s.key}" if s.type == "bool" else f"m:{s.key}"
        rows.append([_btn(_row_label(s, config, overrides), data)])
    rows.append([_btn("🔄 Refresh", "r")])
    text = (
        "⚙️ <b>Flatfinder settings — personal to this chat</b>\n"
        "Tap to change. ★ = changed here (Reset in the submenu reverts to "
        "config.yaml)."
    )
    if is_primary and len(GLOBAL_KEYS) > 0:
        text += (
            f"\nThe last {len(GLOBAL_KEYS)} rows are <i>shared hunt settings</i> "
            "(admin only — they affect every user's scrape)."
        )
    text += "\n<i>Taps queue until the next run (~2h) — it applies them, then confirms.</i>"
    return text, {"inline_keyboard": rows}


def build_submenu(
    setting: Setting, config: AppConfig, overrides: dict[str, Any]
) -> tuple[str, dict]:
    """(text, keyboard) for one key's stepper/preset card."""
    current = _get(config, setting.key)
    star = " ★" if setting.key in overrides else ""
    rows: list[list[dict]] = []
    if setting.type in {"int", "float"} and setting.step:
        minus: list[dict] = []
        plus: list[dict] = []
        if setting.big and setting.big != setting.step:
            minus.append(_btn(f"−{_fmt(setting.big)}", f"a:{setting.key}:-{setting.big:g}"))
        minus.append(_btn(f"−{_fmt(setting.step)}", f"a:{setting.key}:-{setting.step:g}"))
        plus.append(_btn(f"+{_fmt(setting.step)}", f"a:{setting.key}:{setting.step:g}"))
        if setting.big and setting.big != setting.step:
            plus.append(_btn(f"+{_fmt(setting.big)}", f"a:{setting.key}:{setting.big:g}"))
        rows.append(minus + plus)
    elif setting.type == "time":
        rows.append([_btn(c, f"v:{setting.key}:{c}") for c in setting.choices])
    elif setting.type == "date":
        rows.append(
            [
                _btn("−1 week", f"w:{setting.key}:-7"),
                _btn("+1 week", f"w:{setting.key}:7"),
                _btn("clear", f"u:{setting.key}"),
            ]
        )
    rows.append([_btn("↩ Reset to config.yaml", f"u:{setting.key}"), _btn("« Back", "r")])
    text = (
        f"{setting.emoji} <b>{setting.label}</b> — now <b>{_fmt(current)}</b>{star}\n"
        f"<code>{setting.key}</code>"
    )
    if setting.type == "date":
        text += "\nOr type it: <code>/movein 2026-09-01</code>"
    return text, {"inline_keyboard": rows}


def handle_callback(
    data: str, config: AppConfig, overrides: dict[str, Any], *, is_primary: bool
) -> tuple[str, bool, str]:
    """Process one menu tap against ONE chat's overrides.

    Returns (answer_text, overrides_changed, view) where view is "root" or a
    setting key — the card the message should now show. `config` must be the
    chat's current EFFECTIVE config (base + overrides)."""
    parts = (data or "").split(":", 2)
    action = parts[0]
    if action == "r" or len(parts) < 2:
        return "", False, "root"
    setting = SETTINGS_BY_KEY.get(parts[1])
    if setting is None:
        return "That setting no longer exists.", False, "root"
    if setting.scope == SCOPE_GLOBAL and not is_primary:
        return "Admin-only setting.", False, "root"
    key = setting.key
    current = _get(config, key)

    if action == "m":
        return "", False, key
    if action == "t" and setting.type == "bool":
        overrides[key] = not bool(current)
        return f"{setting.label} → {_fmt(overrides[key])}", True, "root"
    if action == "u":
        if key in overrides:
            overrides.pop(key)
            return f"{setting.label} reset.", True, "root"
        if setting.clearable and current is not None:
            overrides[key] = None
            return f"{setting.label} cleared.", True, "root"
        return "Already on the config.yaml value.", False, "root"
    if action == "a" and setting.type in {"int", "float"} and len(parts) == 3:
        try:
            delta = float(parts[2])
        except ValueError:
            return "Bad button data.", False, key
        value = float(current or 0) + delta
        if setting.min is not None:
            value = max(setting.min, value)
        if setting.max is not None:
            value = min(setting.max, value)
        overrides[key] = int(value) if setting.type == "int" else value
        return f"{setting.label} → {_fmt(overrides[key])}", True, key
    if action == "v" and len(parts) == 3:
        try:
            overrides[key] = coerce_value(setting, parts[2])
        except ValueError as e:
            return str(e), False, key
        return f"{setting.label} → {_fmt(overrides[key])}", True, key
    if action == "w" and setting.type == "date" and len(parts) == 3:
        try:
            days = int(parts[2])
        except ValueError:
            return "Bad button data.", False, key
        try:
            start = date.fromisoformat(str(current)) if current else date.today()
        except ValueError:
            start = date.today()
        overrides[key] = (start + timedelta(days=days)).isoformat()
        return f"{setting.label} → {overrides[key]}", True, key
    return "Unrecognised tap.", False, "root"


# ---------------------------------------------------------------------------
# Typed commands (still supported alongside the menu)
# ---------------------------------------------------------------------------


def settings_text(config: AppConfig, overrides: dict[str, Any], *, is_primary: bool) -> str:
    lines = ["🛠 <b>Current settings (this chat)</b>"]
    for s in SETTABLE:
        if s.scope == SCOPE_GLOBAL and not is_primary:
            continue
        shared = " (shared)" if s.scope == SCOPE_GLOBAL else ""
        star = " ★" if s.key in overrides else ""
        lines.append(f"{s.key} = <b>{_fmt(_get(config, s.key))}</b>{star}{shared}")
    if overrides:
        lines.append("★ = set from this chat (/unset <key> to revert)")
    lines.append("/menu for the tappable card · /help for commands.")
    return "\n".join(lines)


def help_text(*, is_primary: bool) -> str:
    shortcuts = " · ".join(
        f"/{a}"
        for a in ("budget", "commute", "livingroom", "shortterm", "movein", "tabs", "double", "bills")
    )
    keys = ", ".join(
        s.key for s in SETTABLE if is_primary or s.scope == SCOPE_PERSONAL
    )
    text = (
        "🛠 <b>Flatfinder settings</b>\n"
        "Your settings only change YOUR shortlist — every allowed chat has its "
        "own. Commands and taps queue until the start of the next run (~2h).\n\n"
        "/menu — tappable settings card\n"
        "/settings — show current values\n"
        "/set &lt;key&gt; &lt;value&gt; — change a setting\n"
        "/unset &lt;key&gt; — revert to config.yaml (/unset all — revert everything)\n\n"
        f"Shortcuts: {shortcuts}\n"
        "e.g. <code>/livingroom on</code> · <code>/budget 1400</code> · "
        "<code>/movein 2026-09-01</code>\n\n"
        f"Keys: {keys}"
    )
    if is_primary:
        text += "\n\nShared hunt settings (admin only): " + ", ".join(GLOBAL_KEYS)
    return text


def handle_command(
    text: str, config: AppConfig, overrides: dict[str, Any], *, is_primary: bool = True
) -> tuple[str | None, bool, bool]:
    """Process one chat message against ONE chat's overrides.

    Returns (reply, overrides_changed, wants_menu). reply=None means the
    message wasn't for us (plain text) — consumed, no response. wants_menu
    asks the caller to send this chat a fresh menu card."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, False, False

    parts = text.split()
    # Telegram appends @BotName in group chats — strip it.
    cmd = parts[0].split("@")[0].lstrip("/").lower()
    args = parts[1:]

    if cmd in {"menu", "start"}:
        return None, False, True
    if cmd == "help":
        return help_text(is_primary=is_primary), False, False
    if cmd in {"settings", "show", "config"}:
        return settings_text(config, overrides, is_primary=is_primary), False, True

    if cmd == "unset":
        if not args:
            return "Usage: /unset <key>  (or /unset all)", False, False
        if args[0].lower() == "all":
            if not overrides:
                return "No chat overrides set — everything is on config.yaml values.", False, False
            overrides.clear()
            return "✅ Cleared this chat's overrides — back to config.yaml values.", True, False
        setting = resolve_key(args[0])
        if setting is None:
            return f"Unknown setting '{args[0]}' — /settings lists the keys.", False, False
        if setting.key not in overrides:
            return f"{setting.key} wasn't overridden — nothing to unset.", False, False
        overrides.pop(setting.key)
        return f"✅ {setting.key} reverted to the config.yaml value.", True, False

    if cmd == "set":
        if len(args) < 2:
            return "Usage: /set <key> <value> — e.g. /set budget.max_pcm 1400", False, False
        key_raw, value_raw = args[0], " ".join(args[1:])
    else:
        # Shortcut form: /livingroom on, /budget 1400 …
        setting = resolve_key(cmd)
        if setting is None:
            return f"Unknown command /{cmd} — send /help for what I understand.", False, False
        if not args:
            return (
                f"{setting.key} is currently <b>{_fmt(_get(config, setting.key))}</b>. "
                f"Send /{cmd} &lt;value&gt; to change it, or /menu to tap."
            ), False, False
        key_raw, value_raw = cmd, " ".join(args)

    setting = resolve_key(key_raw)
    if setting is None:
        return f"Unknown setting '{key_raw}' — /settings lists the keys.", False, False
    if setting.scope == SCOPE_GLOBAL and not is_primary:
        return (
            f"⚠️ {setting.key} is a shared hunt setting — only the admin chat can "
            "change it. Your personal keys: /settings."
        ), False, False
    try:
        value = coerce_value(setting, value_raw)
    except ValueError as e:
        return f"⚠️ {setting.key}: {e}", False, False
    overrides[setting.key] = value
    return (
        f"✅ {setting.label}: {setting.key} → <b>{_fmt(value)}</b> — applies from this run.",
        True,
        False,
    )


# ---------------------------------------------------------------------------
# The per-run sync
# ---------------------------------------------------------------------------


def _drain_updates(notifier: TelegramNotifier, last_id: int | None) -> list[dict]:
    """Fetch everything queued since last_id (paged; getUpdates caps at 100)."""
    updates: list[dict] = []
    offset = (last_id + 1) if last_id is not None else None
    for _ in range(10):  # hard stop — never loop forever on a weird API response
        batch = notifier.get_updates(offset=offset)
        if not batch:
            break
        updates.extend(batch)
        offset = max(int(u.get("update_id") or 0) for u in batch) + 1
        if len(batch) < 100:
            break
    return updates


def sync_remote_settings(
    config: AppConfig,
    env: EnvSettings,
    db: Database,
    *,
    poll: bool = True,
    progress: Callable[[str], None] | None = None,
) -> RemoteSettings:
    """Entry point called at the start of a daily run.

    Drains pending chat messages and menu taps (when poll=True and the bot is
    configured), applies them to the right chat's stored overrides, replies /
    answers / refreshes menu cards, and returns the per-user effective configs
    plus the permissive envelope for the shared scrape. Everything is
    fail-open: a Telegram hiccup means settings simply stay as they were."""
    chats = allowed_chat_ids(env)
    if not chats:
        # No bot configured: nothing personal to resolve; envelope = config.yaml.
        return RemoteSettings(chats=[], primary=None, per_chat={}, envelope=config)

    _migrate_legacy_overrides(db, chats[0])
    overrides_by_chat = {c: load_overrides(db, c) for c in chats}

    if poll and env.telegram_bot_token:
        notifier = TelegramNotifier(env.telegram_bot_token, chats[0])
        try:
            _process_updates(notifier, db, config, chats, overrides_by_chat, progress)
        finally:
            notifier.close()

    state = build_effective_configs(config, chats, overrides_by_chat)
    if progress:
        for chat in chats:
            applied = apply_overrides(config.model_copy(deep=True), overrides_by_chat.get(chat, {}))
            if applied:
                who = "primary" if chat == state.primary else f"chat {chat[-4:]}"
                progress(f"Chat overrides ({who}): " + ", ".join(applied))
    return state


def _process_updates(
    notifier: TelegramNotifier,
    db: Database,
    base_config: AppConfig,
    chats: list[str],
    overrides_by_chat: dict[str, dict[str, Any]],
    progress: Callable[[str], None] | None,
) -> None:
    """Drain + apply queued messages/taps, then persist and respond."""
    primary = chats[0]
    raw_last = db.get_state(STATE_LAST_UPDATE_ID)
    last_id = int(raw_last) if raw_last and raw_last.lstrip("-").isdigit() else None
    updates = _drain_updates(notifier, last_id)
    if not updates:
        return

    def effective(chat: str) -> AppConfig:
        cfg = base_config.model_copy(deep=True)
        apply_overrides(cfg, overrides_by_chat.get(primary, {}), scopes=(SCOPE_GLOBAL,))
        apply_overrides(cfg, overrides_by_chat.get(chat, {}), scopes=(SCOPE_PERSONAL,))
        return cfg

    replies: dict[str, list[str]] = {c: [] for c in chats}
    menu_chats: set[str] = set()  # chats that asked for a fresh card
    cards: dict[tuple[str, int], str] = {}  # (chat, message_id) → view to render
    changed: set[str] = set()
    handled = 0
    max_id = last_id

    for u in updates:
        uid = int(u.get("update_id") or 0)
        max_id = uid if max_id is None else max(max_id, uid)

        cq = u.get("callback_query")
        if cq:
            msg = cq.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id") or "")
            if chat_id not in chats:
                continue  # not an allowed chat — consume silently
            answer, did_change, view = handle_callback(
                str(cq.get("data") or ""),
                effective(chat_id),
                overrides_by_chat[chat_id],
                is_primary=chat_id == primary,
            )
            notifier.answer_callback(str(cq.get("id") or ""), answer)
            if did_change:
                changed.add(chat_id)
            mid = msg.get("message_id")
            if isinstance(mid, int):
                cards[(chat_id, mid)] = view
            handled += 1
            continue

        msg = u.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        if chat_id not in chats:
            continue  # not an allowed chat — consume silently
        reply, did_change, wants_menu = handle_command(
            msg.get("text") or "",
            effective(chat_id),
            overrides_by_chat[chat_id],
            is_primary=chat_id == primary,
        )
        if did_change:
            changed.add(chat_id)
        if reply:
            replies[chat_id].append(reply)
            handled += 1
        if wants_menu:
            menu_chats.add(chat_id)
            handled += 1

    for chat_id in changed:
        save_overrides(db, chat_id, overrides_by_chat[chat_id])
    if max_id is not None and str(max_id) != raw_last:
        db.set_state(STATE_LAST_UPDATE_ID, str(max_id))

    # Respond only after state is safely persisted.
    try:
        for chat_id, chat_replies in replies.items():
            # One message per command keeps each reply tappable/quotable, but
            # cap the burst so a backlog can't rate-limit the run.
            for reply in chat_replies[:10]:
                notifier.send_text(reply, preview=False, chat_id=chat_id)
            if len(chat_replies) > 10:
                notifier.send_text(
                    f"…and {len(chat_replies) - 10} more command(s) processed.",
                    chat_id=chat_id,
                )
        # Refresh every tapped menu card in place to its final state.
        for (chat_id, message_id), view in cards.items():
            cfg = effective(chat_id)
            ovr = overrides_by_chat[chat_id]
            is_prim = chat_id == primary
            setting = SETTINGS_BY_KEY.get(view)
            if setting is not None:
                text, kb = build_submenu(setting, cfg, ovr)
            else:
                text, kb = build_menu(cfg, ovr, is_primary=is_prim)
            if not notifier.edit_message(chat_id, message_id, text, reply_markup=kb):
                menu_chats.add(chat_id)  # edit failed (old card) → send a fresh one
        for chat_id in menu_chats:
            text, kb = build_menu(
                effective(chat_id), overrides_by_chat[chat_id], is_primary=chat_id == primary
            )
            notifier.send_text(text, preview=False, chat_id=chat_id, reply_markup=kb)
    except NotifyError as e:
        # Changes were processed & persisted; only the confirmations failed.
        logger.warning("Telegram settings replies failed: %s", e)

    if handled and progress:
        progress(f"Telegram: handled {handled} command(s)/tap(s) across {len(chats)} chat(s)")
