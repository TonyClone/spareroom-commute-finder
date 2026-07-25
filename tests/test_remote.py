"""Telegram settings: per-user overrides, menu card callbacks, envelope config."""

from __future__ import annotations

import json

import pytest

from flatfinder.config import AppConfig, EnvSettings
from flatfinder.db import Database
from flatfinder.models import FailReason, JourneyResult, Listing, ScoredListing
from flatfinder.rank import rescore_for_user
from flatfinder.remote import (
    STATE_LAST_UPDATE_ID,
    STATE_LEGACY_OVERRIDES,
    allowed_chat_ids,
    apply_overrides,
    build_effective_configs,
    build_menu,
    build_submenu,
    coerce_value,
    handle_callback,
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
# Chat allowlist
# ---------------------------------------------------------------------------


def test_allowed_chat_ids_parses_lists():
    def env_for(raw: str) -> EnvSettings:
        return EnvSettings(telegram_chat_id=raw, _env_file=None)

    assert allowed_chat_ids(env_for("42")) == ["42"]
    assert allowed_chat_ids(env_for("42, 77")) == ["42", "77"]
    assert allowed_chat_ids(env_for("42;77;42")) == ["42", "77"]  # deduped, order kept
    assert allowed_chat_ids(env_for("")) == []


# ---------------------------------------------------------------------------
# Typed command handling
# ---------------------------------------------------------------------------


def test_set_command_and_alias_shortcut():
    cfg = AppConfig()
    overrides: dict = {}
    reply, changed, _menu = handle_command("/set budget.max_pcm 1400", cfg, overrides)
    assert changed and overrides["budget.max_pcm"] == 1400
    assert "1400" in reply

    reply, changed, _menu = handle_command("/livingroom on", cfg, overrides)
    assert changed and overrides["filter.require_living_room"] is True
    assert "✅" in reply


def test_menu_command_requests_card():
    reply, changed, wants_menu = handle_command("/menu", AppConfig(), {})
    assert wants_menu and not changed and reply is None
    reply, changed, wants_menu = handle_command("/start", AppConfig(), {})
    assert wants_menu


def test_global_keys_blocked_for_non_primary():
    overrides: dict = {}
    reply, changed, _ = handle_command(
        "/set search.max_pages 50", AppConfig(), overrides, is_primary=False
    )
    assert not changed and overrides == {} and "admin" in reply.lower()
    reply, changed, _ = handle_command(
        "/set search.max_pages 50", AppConfig(), overrides, is_primary=True
    )
    assert changed and overrides["search.max_pages"] == 50


def test_botname_suffix_and_unset():
    cfg = AppConfig()
    overrides: dict = {}
    _reply, changed, _ = handle_command("/livingroom@FlatfinderBot off", cfg, overrides)
    assert changed and overrides["filter.require_living_room"] is False
    _reply, changed, _ = handle_command("/unset livingroom", cfg, overrides)
    assert changed and overrides == {}
    reply, changed, _ = handle_command("/unset all", cfg, overrides)
    assert not changed  # nothing left to clear


def test_unknown_key_bad_value_and_plain_text():
    overrides: dict = {}
    reply, changed, _ = handle_command("/set office.postcode E1 6AN", AppConfig(), overrides)
    assert not changed and "Unknown setting" in reply
    reply, changed, _ = handle_command("/set daily.max_tabs lots", AppConfig(), overrides)
    assert not changed and "⚠️" in reply
    reply, changed, _ = handle_command("thanks bot!", AppConfig(), overrides)
    assert reply is None and not changed
    assert overrides == {}


def test_settings_text_scopes_and_stars():
    cfg = AppConfig()
    text = settings_text(cfg, {"budget.max_pcm": 1400}, is_primary=True)
    assert "budget.max_pcm" in text and "★" in text and "search.max_pages" in text
    text2 = settings_text(cfg, {}, is_primary=False)
    assert "search.max_pages" not in text2  # global keys hidden for non-primary


# ---------------------------------------------------------------------------
# Menu card + callbacks
# ---------------------------------------------------------------------------


def test_build_menu_rows_and_scope():
    cfg = AppConfig()
    text, kb = build_menu(cfg, {"budget.max_pcm": 1400}, is_primary=True)
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert any("Max rent" in t and "★" in t for t in labels)
    assert any("Search pages" in t for t in labels)  # global row for primary
    _text, kb2 = build_menu(cfg, {}, is_primary=False)
    labels2 = [b["text"] for row in kb2["inline_keyboard"] for b in row]
    assert not any("Search pages" in t for t in labels2)  # hidden for others


def test_callback_toggle_and_reset():
    cfg = AppConfig()  # require_living_room defaults True
    overrides: dict = {}
    answer, changed, view = handle_callback(
        "t:filter.require_living_room", cfg, overrides, is_primary=True
    )
    assert changed and overrides["filter.require_living_room"] is False
    assert "off" in answer and view == "root"
    # Effective config for the next tap reflects the override:
    cfg2 = AppConfig()
    apply_overrides(cfg2, overrides)
    answer, changed, _ = handle_callback(
        "t:filter.require_living_room", cfg2, overrides, is_primary=True
    )
    assert overrides["filter.require_living_room"] is True

    answer, changed, _ = handle_callback(
        "u:filter.require_living_room", cfg2, overrides, is_primary=True
    )
    assert changed and "filter.require_living_room" not in overrides


def test_callback_adjust_clamps_to_range():
    cfg = AppConfig()
    cfg.commute.max_minutes = 175
    overrides: dict = {}
    answer, changed, view = handle_callback(
        "a:commute.max_minutes:10", cfg, overrides, is_primary=True
    )
    assert changed and overrides["commute.max_minutes"] == 180  # clamped to max
    assert view == "commute.max_minutes"  # stays on the stepper card


def test_callback_time_preset_and_global_guard():
    overrides: dict = {}
    answer, changed, _ = handle_callback(
        "v:commute.time:08:30", AppConfig(), overrides, is_primary=True
    )
    assert changed and overrides["commute.time"] == "08:30"
    answer, changed, _ = handle_callback(
        "v:commute.time:09:30", AppConfig(), {}, is_primary=False
    )
    assert not changed and "Admin" in answer


def test_callback_date_shift_and_submenu_view():
    overrides = {"preferences.ideal_move_in": "2026-09-01"}
    cfg = AppConfig()
    apply_overrides(cfg, overrides)
    _answer, changed, _ = handle_callback(
        "w:preferences.ideal_move_in:7", cfg, overrides, is_primary=True
    )
    assert changed and overrides["preferences.ideal_move_in"] == "2026-09-08"
    _answer, _changed, view = handle_callback(
        "m:budget.max_pcm", cfg, overrides, is_primary=True
    )
    assert view == "budget.max_pcm"
    text, kb = build_submenu(resolve_key("budget"), cfg, overrides)
    datas = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    assert "a:budget.max_pcm:-50" in datas and "a:budget.max_pcm:100" in datas
    assert "r" in datas  # back button


# ---------------------------------------------------------------------------
# Effective configs + envelope
# ---------------------------------------------------------------------------


def test_per_user_configs_and_envelope():
    base = AppConfig()
    base.budget.max_pcm = 1450
    state = build_effective_configs(
        base,
        ["42", "77"],
        {
            "42": {"budget.max_pcm": 1200, "filter.require_living_room": True, "search.max_pages": 40},
            "77": {"budget.max_pcm": 1600, "filter.require_living_room": False, "commute.max_minutes": 45},
        },
    )
    a, b = state.per_chat["42"], state.per_chat["77"]
    # Personal isolation:
    assert a.budget.max_pcm == 1200 and b.budget.max_pcm == 1600
    assert a.filter.require_living_room is True and b.filter.require_living_room is False
    # Global key from primary applies to BOTH users' shared scrape:
    assert a.search.max_pages == 40 and b.search.max_pages == 40
    # Envelope is the permissive union:
    env_cfg = state.envelope
    assert env_cfg.budget.max_pcm == 1600
    assert env_cfg.commute.max_minutes == 45
    assert env_cfg.filter.require_living_room is False  # one user keeps no-lounge flats
    assert env_cfg.budget.max_pw >= 1600 * 12 / 52  # search cap admits biggest budget
    assert state.primary == "42"


def test_global_override_from_non_primary_is_ignored():
    state = build_effective_configs(
        AppConfig(), ["42", "77"], {"77": {"search.max_pages": 99}}
    )
    assert state.per_chat["42"].search.max_pages != 99
    assert state.per_chat["77"].search.max_pages != 99


def test_rescore_for_user_refilters_envelope_results():
    ok = JourneyResult(status="OK", duration_minutes=40, transfers=1)
    items = [
        ScoredListing(
            listing=Listing(id="1", url="u1", price_pcm=1500, postcode="E2 8AA"),
            journey=ok,
            filter_pass=True,
            fail_reason=FailReason.OK,
        ),
        ScoredListing(
            listing=Listing(id="2", url="u2", price_pcm=1100, postcode="E2 8AA"),
            journey=JourneyResult(status="OK", duration_minutes=25, transfers=0),
            filter_pass=True,
            fail_reason=FailReason.OK,
        ),
        ScoredListing(  # AI verdicts survive re-scoring for every user
            listing=Listing(id="3", url="u3", price_pcm=1100, postcode="E2 8AA"),
            journey=ok,
            filter_pass=False,
            fail_reason=FailReason.AI_REJECTED,
        ),
    ]
    strict = AppConfig()
    strict.budget.max_pcm = 1200
    strict.commute.max_minutes = 30
    kept = [s for s in rescore_for_user(items, strict) if s.filter_pass]
    assert [s.listing.id for s in kept] == ["2"]


# ---------------------------------------------------------------------------
# End-to-end sync with a fake Telegram API
# ---------------------------------------------------------------------------


class FakeNotifier:
    """Stands in for TelegramNotifier: canned updates, records all sends."""

    updates: list[dict] = []
    sent: list[tuple[str | None, str, dict | None]] = []  # (chat_id, text, markup)
    edits: list[tuple[str, int, str]] = []
    answered: list[tuple[str, str]] = []
    edit_ok: bool = True

    def __init__(self, bot_token: str, chat_id: str, **kwargs):
        self.chat_id = chat_id

    def get_updates(self, offset=None, limit=100):
        batch = [
            u for u in type(self).updates if offset is None or u["update_id"] >= offset
        ]
        return batch[:limit]

    def send_text(self, text, *, preview=True, silent=False, chat_id=None, reply_markup=None):
        type(self).sent.append((chat_id or self.chat_id, text, reply_markup))

    def answer_callback(self, callback_id, text=""):
        type(self).answered.append((callback_id, text))

    def edit_message(self, chat_id, message_id, text, *, reply_markup=None):
        type(self).edits.append((chat_id, message_id, text))
        return type(self).edit_ok

    def close(self):
        pass


def _msg(update_id: int, text: str, chat_id: str = "42") -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": int(chat_id)}, "text": text}}


def _tap(update_id: int, data: str, chat_id: str = "42", message_id: int = 900) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": int(chat_id)}},
        },
    }


@pytest.fixture()
def fake_telegram(monkeypatch):
    FakeNotifier.updates = []
    FakeNotifier.sent = []
    FakeNotifier.edits = []
    FakeNotifier.answered = []
    FakeNotifier.edit_ok = True
    monkeypatch.setattr("flatfinder.remote.TelegramNotifier", FakeNotifier)
    return FakeNotifier


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


def _env(chat_id: str = "42") -> EnvSettings:
    return EnvSettings(telegram_bot_token="t", telegram_chat_id=chat_id, _env_file=None)


def test_sync_applies_commands_per_chat(fake_telegram, db):
    fake_telegram.updates = [
        _msg(1, "/budget 1400", chat_id="42"),
        _msg(2, "/budget 1700", chat_id="77"),
        _msg(3, "hello?", chat_id="42"),  # plain text: consumed, no reply
        _msg(4, "/set daily.max_tabs 5", chat_id="666"),  # stranger: ignored
    ]
    state = sync_remote_settings(AppConfig(), _env("42,77"), db)

    assert state.per_chat["42"].budget.max_pcm == 1400
    assert state.per_chat["77"].budget.max_pcm == 1700
    assert state.envelope.budget.max_pcm == 1700  # permissive union
    assert state.per_chat["42"].daily.max_tabs != 5  # stranger's command dropped

    assert json.loads(db.get_state("telegram_config_overrides:42")) == {"budget.max_pcm": 1400}
    assert json.loads(db.get_state("telegram_config_overrides:77")) == {"budget.max_pcm": 1700}
    assert db.get_state(STATE_LAST_UPDATE_ID) == "4"
    # One confirmation each, delivered to the right chat:
    by_chat = {c: t for c, t, _m in fake_telegram.sent}
    assert "1400" in by_chat["42"] and "1700" in by_chat["77"]


def test_sync_callback_taps_edit_the_card(fake_telegram, db):
    fake_telegram.updates = [
        _tap(1, "t:filter.require_living_room", chat_id="42", message_id=900),
        _tap(2, "a:budget.max_pcm:100", chat_id="42", message_id=900),
        _tap(3, "t:filter.double_only", chat_id="666"),  # stranger: ignored
    ]
    state = sync_remote_settings(AppConfig(), _env("42"), db)

    assert state.per_chat["42"].filter.require_living_room is False
    assert state.per_chat["42"].budget.max_pcm == AppConfig().budget.max_pcm + 100
    assert len(fake_telegram.answered) == 2  # both taps acknowledged
    # The card was refreshed once, in place, to its final state:
    assert len(fake_telegram.edits) == 1
    chat, mid, _text = fake_telegram.edits[0]
    assert (chat, mid) == ("42", 900)


def test_sync_menu_command_sends_card_with_keyboard(fake_telegram, db):
    fake_telegram.updates = [_msg(1, "/menu")]
    sync_remote_settings(AppConfig(), _env(), db)
    cards = [(c, m) for c, _t, m in fake_telegram.sent if m is not None]
    assert cards and cards[0][0] == "42" and "inline_keyboard" in cards[0][1]


def test_sync_failed_edit_falls_back_to_fresh_card(fake_telegram, db):
    fake_telegram.edit_ok = False
    fake_telegram.updates = [_tap(1, "t:filter.double_only")]
    sync_remote_settings(AppConfig(), _env(), db)
    cards = [(c, m) for c, _t, m in fake_telegram.sent if m is not None]
    assert cards  # a fresh menu card was sent since the edit failed


def test_sync_is_exactly_once_across_runs(fake_telegram, db):
    fake_telegram.updates = [_msg(7, "/tabs 5")]
    sync_remote_settings(AppConfig(), _env(), db)
    fake_telegram.sent.clear()

    # Next run: same queue still on the API (Telegram keeps updates ~24h) —
    # the stored offset must prevent reprocessing/re-replying.
    state = sync_remote_settings(AppConfig(), _env(), db)
    assert state.per_chat["42"].daily.max_tabs == 5  # override persisted…
    assert fake_telegram.sent == []  # …but the command wasn't re-handled


def test_sync_without_poll_still_applies_stored_overrides(fake_telegram, db):
    db.set_state("telegram_config_overrides:42", json.dumps({"commute.max_minutes": 40}))
    state = sync_remote_settings(AppConfig(), _env(), db, poll=False)
    assert state.per_chat["42"].commute.max_minutes == 40
    assert fake_telegram.sent == []


def test_legacy_global_overrides_migrate_to_primary(fake_telegram, db):
    db.set_state(STATE_LEGACY_OVERRIDES, json.dumps({"budget.max_pcm": 1300}))
    state = sync_remote_settings(AppConfig(), _env("42,77"), db, poll=False)
    assert state.per_chat["42"].budget.max_pcm == 1300  # primary inherited it
    assert state.per_chat["77"].budget.max_pcm != 1300  # not the second user
    assert not db.get_state(STATE_LEGACY_OVERRIDES)


def test_unset_falls_back_to_config_value_same_run(fake_telegram, db):
    db.set_state("telegram_config_overrides:42", json.dumps({"budget.max_pcm": 1400}))
    fake_telegram.updates = [_msg(1, "/unset budget")]
    base = AppConfig()
    base.budget.max_pcm = 1500  # the config.yaml value
    state = sync_remote_settings(base, _env(), db)
    assert state.per_chat["42"].budget.max_pcm == 1500
    assert load_overrides(db, "42") == {}


def test_corrupt_state_is_ignored(db):
    db.set_state("telegram_config_overrides:42", "{not json")
    assert load_overrides(db, "42") == {}
