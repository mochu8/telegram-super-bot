import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from groupbot.config import ConfigError, build_config
from groupbot.db import Database
from groupbot.groupsettings import (
    AutoReply,
    group_access_decision,
    match_reply,
    mutate_keywords,
    parse_buttons,
)
from groupbot.panel_server import PanelState, create_panel_app
from groupbot.permissions import AdminCache
from groupbot.timeutil import isoformat, utc_now
from groupbot.webapp_auth import AuthError, SlidingLimiter, validate_init_data

TOKEN = "123456:TEST"
ROOT = Path(__file__).resolve().parents[1]


def sign_init(token: str, fields: dict[str, str]) -> str:
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signed = dict(fields)
    signed["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(signed)


def user_fields(user_id: int, auth_date: int, start_param: str | None = None) -> dict[str, str]:
    fields = {
        "auth_date": str(auth_date),
        "query_id": "query",
        "user": json.dumps({"id": user_id, "first_name": "甲<script>", "username": "alice"}),
    }
    if start_param:
        fields["start_param"] = start_param
    return fields


def test_init_data_accepts_a_valid_signature_and_rejects_tampering():
    now = 1_700_000_000
    payload = sign_init(TOKEN, user_fields(5, now, "g-100"))
    identity = validate_init_data(payload, TOKEN, now=now, max_age=100)
    assert identity.user_id == 5
    assert identity.first_name == "甲<script>"
    assert identity.start_param == "g-100"
    with pytest.raises(AuthError):
        validate_init_data(payload, "999:OTHER", now=now, max_age=100)
    tampered = payload.replace("alice", "bob")
    with pytest.raises(AuthError):
        validate_init_data(tampered, TOKEN, now=now, max_age=100)
    expired = sign_init(TOKEN, user_fields(5, now - 500))
    with pytest.raises(AuthError, match="过期"):
        validate_init_data(expired, TOKEN, now=now, max_age=100)
    future = sign_init(TOKEN, user_fields(5, now + 120))
    with pytest.raises(AuthError):
        validate_init_data(future, TOKEN, now=now, max_age=100)
    with pytest.raises(AuthError):
        validate_init_data("", TOKEN, now=now)
    assert group_access_decision("admin") is None
    assert group_access_decision("member") == "forbidden"
    assert group_access_decision("absent") == "forbidden"
    assert group_access_decision("unknown") == "unknown"


def test_keyword_edits_use_the_same_rules_as_commands():
    patch, error = mutate_keywords({}, ("加群",), "内部词", remove=False)
    assert error is None
    assert patch["extra_keywords"] == ["内部词"]
    removed, error = mutate_keywords(patch, ("加群",), "加群", remove=True)
    assert error is None
    assert "加群" in removed["disabled_keywords"]
    reply = match_reply("请问规则在哪", (AutoReply("规则", "看置顶", "contains"),))
    assert reply == "看置顶"
    assert match_reply("规则", (AutoReply("规则", "完全", "exact"),)) == "完全"
    assert match_reply("这是规则吗", (AutoReply("规则", "完全", "exact"),)) is None
    _buttons, error = parse_buttons(
        [{"text": "点我", "action": "url", "url": "javascript:alert(1)"}]
    )
    assert error
    parsed, error = parse_buttons([{"text": "群规", "action": "rules", "url": ""}])
    assert error is None
    assert parsed[0]["action"] == "rules"


def test_panel_url_must_be_https_and_can_stay_disabled():
    cfg = build_config({}, {"BOT_TOKEN": TOKEN})
    assert cfg.panel_base_url == ""
    with pytest.raises(ConfigError, match="PANEL_BASE_URL"):
        build_config({}, {"BOT_TOKEN": TOKEN, "PANEL_BASE_URL": "http://panel.example"})


def test_panel_page_does_not_inject_html():
    script = (ROOT / "groupbot" / "panel_static" / "app.js").read_text(encoding="utf-8")
    page = (ROOT / "groupbot" / "panel_static" / "index.html").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "innerHTML" not in page
    assert "bot_token" not in script
    assert "BOT_TOKEN" not in page


class FakeBot:
    def __init__(self) -> None:
        self.calls = 0
        self.sent: list[tuple] = []

    async def get_chat_member(self, chat_id, user_id):
        self.calls += 1
        status = "administrator" if (chat_id, user_id) == (-100, 5) else "member"
        return type("Member", (), {"status": status})()

    async def get_chat_member_count(self, chat_id):
        return 8

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))


def test_panel_authorizes_admins_and_shares_settings(tmp_path):
    async def check():
        database = Database(str(tmp_path / "bot.db"))
        await database.connect()
        bot = FakeBot()
        synced: list[int] = []

        async def sync(chat_id: int) -> None:
            synced.append(chat_id)

        try:
            now = isoformat(utc_now())
            await database.ensure_group(-100, "测试群", now)
            await database.ensure_group(-200, "别的群", now)
            cfg = build_config(
                {},
                {
                    "BOT_TOKEN": TOKEN,
                    "ADMIN_IDS": "9",
                    "PANEL_BASE_URL": "https://panel.example",
                    "PANEL_RATE_LIMIT": "8",
                    "PANEL_AUTH_MAX_AGE": "86400",
                },
            )
            state = PanelState(
                cfg=cfg,
                db=database,
                bot=bot,
                admin_cache=AdminCache(),
                limiter=SlidingLimiter(),
                sync_announcements=sync,
            )
            client = TestClient(TestServer(create_panel_app(state)))
            await client.start_server()
            try:
                health = await client.get("/health")
                assert health.status == 200
                assert TOKEN not in await health.text()
                now_stamp = int(time.time())
                admin = {
                    "X-Telegram-Init-Data": sign_init(TOKEN, user_fields(5, now_stamp)),
                }
                member = {
                    "X-Telegram-Init-Data": sign_init(TOKEN, user_fields(6, now_stamp)),
                }
                super_admin = {
                    "X-Telegram-Init-Data": sign_init(TOKEN, user_fields(9, now_stamp)),
                }
                missing = await client.get("/api/me")
                assert missing.status == 401
                bad_origin = await client.get(
                    "/api/me",
                    headers={**admin, "Origin": "https://evil.example"},
                )
                assert bad_origin.status == 403
                me = await client.get("/api/me", headers=admin)
                assert me.status == 200
                body = await me.json()
                assert body["user_id"] == 5
                assert "<script>" in body["first_name"]
                assert "token" not in body

                allowed = await client.get("/api/groups/-100", headers=admin)
                assert allowed.status == 200
                denied = await client.get("/api/groups/-200", headers=member)
                assert denied.status == 403
                calls_before = bot.calls
                elevated = await client.get("/api/groups/-200", headers=super_admin)
                assert elevated.status == 200
                assert bot.calls == calls_before

                added = await client.post(
                    "/api/groups/-100/keywords",
                    json={"word": "内部词", "remove": False},
                    headers=admin,
                )
                assert added.status == 200
                assert "内部词" in (await added.json())["settings"]["keywords"]
                tested = await client.post(
                    "/api/groups/-100/test",
                    json={"text": "今天加新朋友"},
                    headers=admin,
                )
                assert (await tested.json())["violation"] is False
                caught = await client.post(
                    "/api/groups/-100/test",
                    json={"text": "加微信 abcde"},
                    headers=admin,
                )
                assert (await caught.json())["violation"] is True

                plain = await client.post(
                    "/api/groups/-100/keywords",
                    data="{}",
                    headers=admin,
                )
                assert plain.status == 415
            finally:
                await client.close()
        finally:
            await database.close()

    asyncio.run(check())


def test_panel_rate_limit(tmp_path):
    async def check():
        database = Database(str(tmp_path / "rate.db"))
        await database.connect()
        try:
            cfg = build_config(
                {},
                {
                    "BOT_TOKEN": TOKEN,
                    "PANEL_BASE_URL": "https://panel.example",
                    "PANEL_RATE_LIMIT": "2",
                    "PANEL_RATE_WINDOW": "60",
                },
            )
            state = PanelState(
                cfg=cfg,
                db=database,
                bot=FakeBot(),
                admin_cache=AdminCache(),
                limiter=SlidingLimiter(),
                sync_announcements=_noop,
            )
            client = TestClient(TestServer(create_panel_app(state)))
            await client.start_server()
            try:
                headers = {
                    "X-Telegram-Init-Data": sign_init(TOKEN, user_fields(5, int(time.time()))),
                }
                statuses = [(await client.get("/api/me", headers=headers)).status for _ in range(3)]
                assert statuses == [200, 200, 429]
            finally:
                await client.close()
        finally:
            await database.close()

    asyncio.run(check())


async def _noop(_chat_id: int) -> None:
    return None
