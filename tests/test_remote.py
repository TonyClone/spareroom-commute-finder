"""Telegram remote-settings console: command parsing, persistence, application."""

from __future__ import annotations

import json

import pytest

from flatfinder.config import AppConfig, EnvSettings
from flatfinder.db import Database
from flatfinder.remote import (
    STATE_LAST_UPDATE_ID,
    STATE_OVERRIDES,
    apply_overrides,
    coerce_value,
    handle_command,
    load_overrides,
    resolve_key,
    settings_text,
    sync_remote_settings,
)


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


def test_coerce_bool_words():
    s = resolve_key("filter.require_living_room")
    for raw in ("on", "true", "YES", "1"):
        assert coerce_value(s, raw) is True
    for raw in ("off", "false", "No", "0"):
        assert coerce_value(s, raw) is False
    with pytest.raises(ValueError):
        coerce_value(s, "maybe")


def test_coerce_numbers_and_ranges():
    budget = resolve_key("budget")  # alias → budget.max_pcm
    assert budget.key == "budget.max_pcm"
    assert coerce_value(budget, "£1,400") == 1400
    with pytest.raises(ValueError):
        coerce_value(budget, "5")  # below sane floor — catches "/budget 5" typos
    minutes = resolve_key("commute")
    assert coerce_value(minutes, "35") == 35
    assert isinstance(coerce_value(minutes, "35"), int)
    with pytest.raises(ValueError):
        coerce_value(minutes, "999")


def test_coerce_time_and_date():
    t = resolve_key("commute.time")
    assert coerce_value(t, "9:30") == "09:30"
    with pytest.raises(ValueError):
        coerce_value(t, "25:00")
    d = resolve_key("movein")
    assert coerce_value(d, "2026-09-01") == "2026-09-01"
    assert coerce_value(d, "clear") is None
    with pytest.raises(ValueError):
        coerce_value(d, "next tuesday")


# ---------------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------------


def test_set_command_and_alias_shortcut():
    cfg = AppConfig()
    overrides: dict = {}
    reply, changed = handle_command("/set budget.max_pcm 1400", cfg, overrides)
    assert changed and overrides["budget.max_pcm"] == 1400
    assert "1400" in reply

    reply, changed = handle_command("/livingroom on", cfg, overrides)
    assert changed and overrides["filter.require_living_room"] is True
    assert "✅" in reply


def test_shortcut_without_value_shows_current():
    cfg = AppConfig()
    cfg.budget.max_pcm = 1234
    reply, changed = handle_command("/budget", cfg, {})
    assert not changed
    assert "1234" in reply


def test_botname_suffix_is_stripped():
    reply, changed = handle_command("/livingroom@FlatfinderBot off", AppConfig(), {})
    assert changed
    assert "off" in reply


def test_unset_and_unset_all():
    cfg = AppConfig()
    overrides = {"budget.max_pcm": 1400, "daily.max_tabs": 5}
    reply, changed = handle_command("/unset budget", cfg, overrides)
    assert changed and "budget.max_pcm" not in overrides
    reply, changed = handle_command("/unset budget", cfg, overrides)
    assert not changed  # already gone
    reply, changed = handle_command("/unset all", cfg, overrides)
    assert changed and overrides == {}


def test_unknown_key_and_bad_value_are_reported_not_applied():
    overrides: dict = {}
    reply, changed = handle_command("/set office.postcode E1 6AN", AppConfig(), overrides)
    assert not changed and overrides == {} and "Unknown setting" in reply
    reply, changed = handle_command("/set daily.max_tabs lots", AppConfig(), overrides)
    assert not changed and overrides == {} and "⚠️" in reply


def test_plain_text_is_ignored_and_unknown_command_gets_help_hint():
    reply, changed = handle_command("thanks bot!", AppConfig(), {})
    assert reply is None and not changed
    reply, changed = handle_command("/frobnicate", AppConfig(), {})
    assert reply is not None and "/help" in reply and not changed


def test_settings_text_marks_overrides():
    cfg = AppConfig()
    text = settings_text(cfg, {"budget.max_pcm": 1400})
    assert "budget.max_pcm" in text and "★" in text


# ---------------------------------------------------------------------------
# Override application
# ---------------------------------------------------------------------------


def test_apply_overrides_sets_values_and_skips_stale_keys():
    cfg = AppConfig()
    applied = apply_overrides(
        cfg,
        {
            "budget.max_pcm": 1400,
            "filter.require_living_room": False,
            "no.such.key": 1,  # stale/corrupt entry must never crash a run
        },
    )
    assert cfg.budget.max_pcm == 1400
    assert cfg.filter.require_living_room is False
    assert len(applied) == 2


# ---------------------------------------------------------------------------
# End-to-end sync with a fake Telegram API
# ---------------------------------------------------------------------------


class FakeNotifier:
    """Stands in for TelegramNotifier: canned updates, records sends."""

    updates: list[dict] = []
    sent: list[str] = []

    def __init__(self, bot_token: str, chat_id: str, **kwargs):
        self.chat_id = chat_id

    def get_updates(self, offset=None, limit=100):
        batch = [
            u
            for u in type(self).updates
            if offset is None or u["update_id"] >= offset
        ]
        return batch[:limit]

    def send_text(self, text, **kwargs):
        type(self).sent.append(text)

    def close(self):
        pass


def _msg(update_id: int, text: str, chat_id: str = "42") -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": int(chat_id)}, "text": text}}


@pytest.fixture()
def fake_telegram(monkeypatch):
    FakeNotifier.updates = []
    FakeNotifier.sent = []
    monkeypatch.setattr("flatfinder.remote.TelegramNotifier", FakeNotifier)
    return FakeNotifier


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


def _env() -> EnvSettings:
    return EnvSettings(telegram_bot_token="t", telegram_chat_id="42", _env_file=None)


def test_sync_applies_commands_persists_and_replies(fake_telegram, db):
    fake_telegram.updates = [
        _msg(1, "/budget 1400"),
        _msg(2, "/livingroom off"),
        _msg(3, "hello?"),  # plain text: consumed, no reply
        _msg(4, "/set daily.max_tabs 5", chat_id="666"),  # stranger: ignored
    ]
    cfg = sync_remote_settings(AppConfig(), _env(), db)

    assert cfg.budget.max_pcm == 1400
    assert cfg.filter.require_living_room is False
    assert cfg.daily.max_tabs != 5  # stranger's command must not apply

    stored = json.loads(db.get_state(STATE_OVERRIDES))
    assert stored == {"budget.max_pcm": 1400, "filter.require_living_room": False}
    assert db.get_state(STATE_LAST_UPDATE_ID) == "4"
    assert len(fake_telegram.sent) == 2  # one confirmation per command


def test_sync_is_exactly_once_across_runs(fake_telegram, db):
    fake_telegram.updates = [_msg(7, "/tabs 5")]
    sync_remote_settings(AppConfig(), _env(), db)
    fake_telegram.sent.clear()

    # Next run: same queue still on the API (Telegram keeps updates ~24h) —
    # the stored offset must prevent reprocessing/re-replying.
    cfg = sync_remote_settings(AppConfig(), _env(), db)
    assert cfg.daily.max_tabs == 5  # override persisted…
    assert fake_telegram.sent == []  # …but the command wasn't re-handled


def test_sync_without_poll_still_applies_stored_overrides(fake_telegram, db):
    db.set_state(STATE_OVERRIDES, json.dumps({"commute.max_minutes": 40}))
    cfg = sync_remote_settings(AppConfig(), _env(), db, poll=False)
    assert cfg.commute.max_minutes == 40
    assert fake_telegram.sent == []


def test_unset_falls_back_to_config_value_same_run(fake_telegram, db):
    db.set_state(STATE_OVERRIDES, json.dumps({"budget.max_pcm": 1400}))
    fake_telegram.updates = [_msg(1, "/unset budget")]
    base = AppConfig()
    base.budget.max_pcm = 1500  # the config.yaml value
    cfg = sync_remote_settings(base, _env(), db)
    assert cfg.budget.max_pcm == 1500
    assert load_overrides(db) == {}


def test_settings_command_shows_effective_values(fake_telegram, db):
    db.set_state(STATE_OVERRIDES, json.dumps({"budget.max_pcm": 1400}))
    fake_telegram.updates = [_msg(1, "/settings")]
    sync_remote_settings(AppConfig(), _env(), db)
    assert len(fake_telegram.sent) == 1
    assert "1400" in fake_telegram.sent[0]  # override shown, not the yaml value
    assert "★" in fake_telegram.sent[0]


def test_corrupt_state_is_ignored(db):
    db.set_state(STATE_OVERRIDES, "{not json")
    assert load_overrides(db) == {}
