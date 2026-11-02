import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from telegram.error import BadRequest

from groupbot.config import (
    ConfigError,
    build_config,
    load_config,
    load_file,
    resolve_settings,
)
from groupbot.handlers.errors import on_error
from groupbot.helptext import ADMIN_COMMANDS, HELP_TEXT, USER_COMMANDS
from groupbot.jobs import maintenance_plan
from groupbot.main import build_application
from groupbot.permissions import AdminCache
from groupbot.textutil import (
    humanize_telegram_error,
    normalize_domain,
    parse_bool,
    parse_duration,
    render_template,
)
from groupbot.timeutil import clamp_mute_seconds, local_today, mute_until
from groupbot.verify import callback_data, make_challenge, parse_callback_data

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_honors_previously_unused_values_and_is_not_overbroad():
    data = load_file(ROOT / "config.example.json")
    cfg = build_config(data, {})
    assert cfg.mute_duration == 3600
    assert cfg.msg_per_point == 50
    assert cfg.max_points_per_day == 100
    assert cfg.backup_enabled is True
    assert cfg.backup_interval_hours == 24
    assert cfg.dice_reward_threshold == 5
    settings = resolve_settings(cfg)
    assert settings.mute_duration == 3600
    assert settings.msg_per_point == 50
    from groupbot.moderation import Policy, ScanInput, scan

    rules = Policy(keywords=cfg.keywords, patterns=cfg.regex_patterns, ban_links=True)
    assert scan(ScanInput("今天加新朋友"), rules).violation is False
    assert scan(ScanInput("加微信 abcde"), rules).kind == "regex"
    assert "免费" not in cfg.keywords


def test_env_overrides_file_and_missing_token_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        '{"bot_token": "file-token", "admin_ids": [1], "timezone": "Asia/Shanghai"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(path))
    monkeypatch.setenv("BOT_TOKEN", "env-token")
    monkeypatch.setenv("ADMIN_IDS", "5, 6")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    cfg = load_config()
    assert cfg.token == "env-token"
    assert cfg.admin_ids == frozenset({5, 6})

    monkeypatch.delenv("BOT_TOKEN")
    path.write_text('{"bot_token": "YOUR_BOT_TOKEN_HERE"}', encoding="utf-8")
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        load_config()


def test_group_overrides_and_backup_switch():
    cfg = build_config(
        {"log_channel_id": -100, "backup": {"enabled": True, "interval_hours": 12}},
        {"BOT_TOKEN": "1:x"},
    )
    disabled = resolve_settings(
        cfg,
        {
            "log_channel_id": 0,
            "extra_keywords": ["内部词"],
            "disabled_keywords": ["加群"],
            "ban_links": False,
        },
    )
    assert disabled.log_channel_id is None
    assert "内部词" in disabled.keywords
    assert "加群" not in disabled.keywords
    assert disabled.ban_links is False
    assert resolve_settings(cfg).log_channel_id == -100
    names = [name for name, _interval in maintenance_plan(cfg)]
    assert ("db-backup", timedelta(hours=12)) in maintenance_plan(cfg)
    off = build_config({"backup": {"enabled": False}}, {"BOT_TOKEN": "1:x"})
    assert [name for name, _interval in maintenance_plan(off)] == ["retention"]
    assert "db-backup" in names
    short_flood = build_config({"anti_flood": {"mute_seconds": 10}}, {"BOT_TOKEN": "1:x"})
    assert resolve_settings(short_flood).flood_mute == 30


def test_render_template_escapes_user_text_once():
    rendered = render_template("你好 {name} <b>群</b>", name="<script>{chat}</script>")
    assert "<script>" not in rendered
    assert "<b>" not in rendered
    assert "&lt;script&gt;{chat}&lt;/script&gt;" in rendered
    assert parse_duration("60") == timedelta(minutes=60)
    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("原因") is None
    assert parse_bool("开") is True
    assert parse_bool("off") is False
    assert normalize_domain("https://WWW.Example.com/a") == "example.com"


def test_mute_until_is_timezone_aware_and_clamped():
    now = datetime(2026, 1, 1, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    until = mute_until(now, 3600)
    assert until is not None
    assert until.tzinfo is not None
    assert until.utcoffset() == timedelta(0)
    assert clamp_mute_seconds(10)[0] == 30
    assert mute_until(now, 0) is None
    shanghai = local_today(datetime(2026, 1, 1, 16, 30, tzinfo=UTC), ZoneInfo("Asia/Shanghai"))
    assert shanghai == datetime(2026, 1, 2).date()


def test_help_lists_every_command_and_the_app_registers_them():
    names = [name for name, _desc in USER_COMMANDS + ADMIN_COMMANDS]
    for name in names:
        assert f"/{name}" in HELP_TEXT
    cfg = build_config({}, {"BOT_TOKEN": "123456:TEST"})
    app = build_application(cfg)
    registered: set[str] = set()
    for handlers in app.handlers.values():
        for handler in handlers:
            commands = getattr(handler, "commands", None)
            if commands:
                registered.update(commands)
    assert set(names) <= registered
    assert app.concurrent_updates == 256
    assert app.job_queue is not None


def test_handlers_do_not_block_on_sleep():
    for path in (ROOT / "groupbot").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "asyncio.sleep" not in text
        assert "time.sleep" not in text


def test_error_handler_logs_without_leaking_to_the_group_or_repeating_the_token():
    sent: list[dict] = []

    class Bot:
        async def send_message(self, *args, **kwargs):
            sent.append({"args": args, "kwargs": kwargs})

    class Application:
        bot_data = {"cfg": None}

    class Context:
        error = RuntimeError("https://api.telegram.org/botSECRET/getMe")
        bot = Bot()
        application = Application()

    class Update:
        effective_chat = type("Chat", (), {"id": 1, "type": "supergroup"})()

    asyncio.run(on_error(Update(), Context()))
    assert sent == []

    class Configured:
        log_channel_id = -100

    Context.application = type("App", (), {"bot_data": {"cfg": Configured()}})()
    asyncio.run(on_error(Update(), Context()))
    assert len(sent) == 1
    body = sent[0]["kwargs"]["text"] if "text" in sent[0]["kwargs"] else sent[0]["args"][-1]
    assert "SECRET" not in body
    assert "RuntimeError" in body
    assert "请确认机器人是管理员" in humanize_telegram_error(RuntimeError("not enough rights"))


def test_admin_cache_and_captcha_callback():
    class Bot:
        def __init__(self):
            self.calls = 0

        async def get_chat_member(self, chat_id, user_id):
            self.calls += 1
            if user_id == 404:
                raise BadRequest("User not found")
            status = "administrator" if user_id == 2 else "member"
            return type("Member", (), {"status": status})()

    bot = Bot()
    cache = AdminCache(ttl=60)

    async def check():
        assert await cache.role(bot, 1, 9, frozenset({9})) == "admin"
        assert await cache.role(bot, 1, 3, frozenset()) == "member"
        assert await cache.role(bot, 1, 3, frozenset()) == "member"
        assert await cache.role(bot, 1, 2, frozenset()) == "admin"
        assert await cache.role(bot, 1, 404, frozenset()) == "absent"
        assert bot.calls == 3

    asyncio.run(check())
    challenge = make_challenge("captcha", __import__("random").Random(1))
    assert challenge.answer in challenge.choices
    assert len(challenge.choices) == 4
    data = callback_data(123456789, challenge.answer)
    assert len(data.encode()) <= 64
    assert parse_callback_data(data) == (123456789, challenge.answer)
    assert make_challenge("button").answer == "ok"
