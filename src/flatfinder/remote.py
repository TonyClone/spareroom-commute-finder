"""Change settings from the Telegram chat — a tiny remote settings console.

The vacation workflow runs headless every couple of hours, so there's no
terminal to open the settings editor in. Instead, the bot chat doubles as the
console: send `/set budget.max_pcm 1400` (or a shortcut like `/livingroom on`)
any time, and the NEXT run picks it up before scraping.

How it works, per daily run (notify mode only):
  1. Drain the bot's `getUpdates` queue. The last processed update_id is stored
     in the DB (`app_state` table), so every command is handled exactly once —
     even across cloud runs, because the DB rides the encrypted runner-state
     branch between workflow runs.
  2. Commands from YOUR chat (TELEGRAM_CHAT_ID) mutate a persisted overrides
     dict; anything from another chat is ignored. Each processed batch gets one
     confirmation reply.
  3. The stored overrides are applied on top of config.yaml for this and every
     later run, until `/unset` removes them.

Overrides never touch config.yaml itself (on cloud runs that file is
regenerated from a secret each run, so writing to it would be pointless).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from flatfinder.config import AppConfig, EnvSettings
from flatfinder.db import Database
from flatfinder.notify import NotifyError, TelegramNotifier

logger = logging.getLogger(__name__)

# app_state keys
STATE_OVERRIDES = "telegram_config_overrides"
STATE_LAST_UPDATE_ID = "telegram_last_update_id"

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

_TRUE_WORDS = {"on", "true", "yes", "y", "1", "enable", "enabled"}
_FALSE_WORDS = {"off", "false", "no", "n", "0", "disable", "disabled"}


@dataclass(frozen=True)
class Setting:
    key: str  # dotted config path
    type: str  # bool | int | float | time | date
    label: str
    min: float | None = None
    max: float | None = None
    clearable: bool = False  # value may be unset with "clear"/"none"


# Every key the chat may change. Deliberately a whitelist: office location and
# scraper politeness knobs stay out of reach of a lost phone.
SETTABLE: list[Setting] = [
    Setting("budget.max_pcm", "float", "Max rent £/month", min=100, max=20_000),
    Setting("budget.min_pcm", "float", "Min rent £/month (0 = no floor)", min=0, max=20_000),
    Setting("budget.max_pw", "float", "Max rent £/week (search cap)", min=25, max=5_000),
    Setting("commute.max_minutes", "int", "Max commute minutes", min=5, max=180),
    Setting("commute.time", "time", "Arrive-by time (HH:MM)"),
    Setting("filter.require_living_room", "bool", "Drop rooms with NO living room"),
    Setting("filter.exclude_short_term", "bool", "Drop short-term-only sublets"),
    Setting("filter.short_term_max_months", "int", "Max term counted as short (months)", min=1, max=12),
    Setting("filter.double_only", "bool", "Double rooms only"),
    Setting("filter.bills_included_only", "bool", "Bills included only"),
    Setting("preferences.ideal_move_in", "date", "Ideal move-in (YYYY-MM-DD)", clearable=True),
    Setting("preferences.late_grace_days", "int", "Days late still 'ok'", min=0, max=365),
    Setting("daily.max_tabs", "int", "Max rooms sent per run", min=1, max=50),
    Setting("daily.living_room_first", "bool", "Living-room rooms first"),
    Setting("daily.move_in_first", "bool", "Closest move-in first"),
    Setting("search.max_pages", "int", "Search pages to scrape", min=1, max=100),
    Setting("search.max_listings", "int", "Max listings per run", min=20, max=2_000),
    Setting("ai.enabled", "bool", "DeepSeek AI filter"),
    Setting("ai.min_score", "int", "AI min score to keep (1-10)", min=1, max=10),
]

SETTINGS_BY_KEY: dict[str, Setting] = {s.key: s for s in SETTABLE}

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
    return str(value)


def load_overrides(db: Database) -> dict[str, Any]:
    raw = db.get_state(STATE_OVERRIDES)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Corrupt %s state — ignoring", STATE_OVERRIDES)
        return {}
    return data if isinstance(data, dict) else {}


def save_overrides(db: Database, overrides: dict[str, Any]) -> None:
    db.set_state(STATE_OVERRIDES, json.dumps(overrides))


def apply_overrides(config: AppConfig, overrides: dict[str, Any]) -> list[str]:
    """Set stored overrides onto the loaded config (in place).

    Returns human-readable notes of what was applied. Unknown/stale keys are
    skipped with a warning, never fatal — a config schema change must not be
    able to kill the hunt."""
    applied: list[str] = []
    for key, value in overrides.items():
        if key not in SETTINGS_BY_KEY:
            logger.warning("Skipping unknown settings override %r", key)
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


def settings_text(config: AppConfig, overrides: dict[str, Any]) -> str:
    lines = ["🛠 <b>Current settings</b>"]
    for s in SETTABLE:
        obj: Any = config
        for part in s.key.split("."):
            obj = getattr(obj, part)
        star = " ★" if s.key in overrides else ""
        lines.append(f"{s.key} = <b>{_fmt(obj)}</b>{star}")
    if overrides:
        lines.append("★ = set from this chat (/unset <key> to revert)")
    lines.append("Change with /set <key> <value> — /help for shortcuts.")
    return "\n".join(lines)


def help_text() -> str:
    shortcuts = " · ".join(
        f"/{a}"
        for a in ("budget", "commute", "livingroom", "shortterm", "movein", "tabs", "double", "bills", "ai")
    )
    keys = ", ".join(s.key for s in SETTABLE)
    return (
        "🛠 <b>Flatfinder remote settings</b>\n"
        "Send a command any time — it's picked up at the start of the next run "
        "(cloud runs every ~2h).\n\n"
        "/settings — show current values\n"
        "/set &lt;key&gt; &lt;value&gt; — change a setting\n"
        "/unset &lt;key&gt; — revert to config.yaml (/unset all — revert everything)\n\n"
        f"Shortcuts: {shortcuts}\n"
        "e.g. <code>/livingroom on</code> · <code>/budget 1400</code> · "
        "<code>/commute 35</code> · <code>/movein 2026-09-01</code>\n\n"
        f"Keys: {keys}"
    )


def handle_command(
    text: str, config: AppConfig, overrides: dict[str, Any]
) -> tuple[str | None, bool]:
    """Process one chat message. Returns (reply, overrides_changed).

    reply=None means the message wasn't for us (plain text, not a command) —
    it is still consumed (offset advances) but gets no response."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, False

    parts = text.split()
    # Telegram appends @BotName in group chats — strip it.
    cmd = parts[0].split("@")[0].lstrip("/").lower()
    args = parts[1:]

    if cmd in {"help", "start"}:
        return help_text(), False
    if cmd in {"settings", "show", "config"}:
        return settings_text(config, overrides), False

    if cmd == "unset":
        if not args:
            return "Usage: /unset <key>  (or /unset all)", False
        if args[0].lower() == "all":
            if not overrides:
                return "No chat overrides set — everything is on config.yaml values.", False
            overrides.clear()
            return "✅ Cleared all chat overrides — back to config.yaml values.", True
        setting = resolve_key(args[0])
        if setting is None:
            return f"Unknown setting '{args[0]}' — /settings lists the keys.", False
        if setting.key not in overrides:
            return f"{setting.key} wasn't overridden — nothing to unset.", False
        overrides.pop(setting.key)
        return f"✅ {setting.key} reverted to the config.yaml value.", True

    if cmd == "set":
        if len(args) < 2:
            return "Usage: /set <key> <value> — e.g. /set budget.max_pcm 1400", False
        key_raw, value_raw = args[0], " ".join(args[1:])
    else:
        # Shortcut form: /livingroom on, /budget 1400 …
        setting = resolve_key(cmd)
        if setting is None:
            return f"Unknown command /{cmd} — send /help for what I understand.", False
        if not args:
            obj: Any = config
            for part in setting.key.split("."):
                obj = getattr(obj, part)
            return (
                f"{setting.key} is currently <b>{_fmt(obj)}</b>. "
                f"Send /{cmd} &lt;value&gt; to change it."
            ), False
        key_raw, value_raw = cmd, " ".join(args)

    setting = resolve_key(key_raw)
    if setting is None:
        return f"Unknown setting '{key_raw}' — /settings lists the keys.", False
    try:
        value = coerce_value(setting, value_raw)
    except ValueError as e:
        return f"⚠️ {setting.key}: {e}", False
    overrides[setting.key] = value
    return (
        f"✅ {setting.label}: {setting.key} → <b>{_fmt(value)}</b> — applies from this run.",
        True,
    )


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
) -> AppConfig:
    """Entry point called at the start of a daily run. Returns the effective
    config: the one passed in (treated as the pristine config.yaml values)
    with the stored chat overrides applied on top.

    Drains pending chat commands (when poll=True and the bot is configured),
    persists the resulting overrides, and replies in the chat. Everything is
    fail-open: a Telegram hiccup means settings simply stay as they were."""
    overrides = load_overrides(db)
    # Command handling reads values off a preview copy that already has the
    # stored overrides, so /settings and "/budget" (no args) show what's
    # actually in effect. `config` itself stays pristine until the end, so an
    # /unset in this very batch cleanly falls back to the config.yaml value.
    preview = config.model_copy(deep=True)
    apply_overrides(preview, overrides)

    if poll and env.telegram_bot_token and env.telegram_chat_id:
        notifier = TelegramNotifier(env.telegram_bot_token, env.telegram_chat_id)
        try:
            raw_last = db.get_state(STATE_LAST_UPDATE_ID)
            last_id = int(raw_last) if raw_last and raw_last.lstrip("-").isdigit() else None
            updates = _drain_updates(notifier, last_id)
            replies: list[str] = []
            changed = False
            max_id = last_id
            for u in updates:
                uid = int(u.get("update_id") or 0)
                max_id = uid if max_id is None else max(max_id, uid)
                msg = u.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id") or "")
                if chat_id != str(env.telegram_chat_id).strip():
                    continue  # not our chat — consume silently
                reply, did_change = handle_command(msg.get("text") or "", preview, overrides)
                if did_change:
                    changed = True
                    # Keep the preview current for later commands in this batch.
                    preview = config.model_copy(deep=True)
                    apply_overrides(preview, overrides)
                if reply:
                    replies.append(reply)
            if changed:
                save_overrides(db, overrides)
            if max_id is not None and str(max_id) != raw_last:
                db.set_state(STATE_LAST_UPDATE_ID, str(max_id))
            if replies:
                # One message per command keeps each reply tappable/quotable,
                # but cap the burst so a backlog can't rate-limit the run.
                for reply in replies[:10]:
                    notifier.send_text(reply, preview=False)
                if len(replies) > 10:
                    notifier.send_text(f"…and {len(replies) - 10} more command(s) processed.")
            if updates and progress:
                progress(f"Telegram: processed {len(replies)} command(s) from chat")
        except NotifyError as e:
            # Commands were processed & persisted; only the confirmations failed.
            logger.warning("Telegram settings replies failed: %s", e)
        finally:
            notifier.close()

    effective = config.model_copy(deep=True)
    applied = apply_overrides(effective, overrides)
    if applied and progress:
        progress("Chat overrides active: " + ", ".join(applied))
    return effective
