"""管理面板的 HTTP 服务。没有 PANEL_BASE_URL 时不会启动。"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from groupbot.config import AppConfig, resolve_settings
from groupbot.db import Database
from groupbot.groupsettings import (
    AUDIT_KINDS,
    BOOL_FIELDS,
    NUMBER_FIELDS,
    VIOLATION_KINDS,
    group_access_decision,
    mutate_domains,
    mutate_keywords,
    mutate_patterns,
    parse_announcements,
    parse_buttons,
    parse_replies,
)
from groupbot.moderation import Policy, ScanInput, scan
from groupbot.permissions import AdminCache
from groupbot.services import settings_dict
from groupbot.timeutil import format_local, isoformat, utc_now
from groupbot.webapp_auth import AuthError, SlidingLimiter, validate_init_data

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).with_name("panel_static")
_IDENTITY: web.RequestKey = web.RequestKey("identity")

SyncJobs = Callable[[int], Awaitable[None]]


@dataclass
class PanelState:
    cfg: AppConfig
    db: Database
    bot: object
    admin_cache: AdminCache
    limiter: SlidingLimiter
    sync_announcements: SyncJobs


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _settings_view(settings) -> dict:
    return {
        "welcome_message": settings.welcome_message,
        "welcome_enabled": settings.welcome_enabled,
        "welcome_buttons": [
            {"text": item.text, "action": item.action, "url": item.url}
            for item in settings.welcome_buttons
        ],
        "keywords": list(settings.keywords),
        "regex_patterns": list(settings.regex_patterns),
        "ban_links": settings.ban_links,
        "whitelist_domains": list(settings.whitelist_domains),
        "new_user_restrict_minutes": settings.new_user_restrict_minutes,
        "mute_duration": settings.mute_duration,
        "ban_channel_messages": settings.ban_channel_messages,
        "ban_forwards": settings.ban_forwards,
        "anti_ad_enabled": settings.anti_ad_enabled,
        "delete_service": settings.delete_service,
        "delete_leave": settings.delete_leave,
        "verify_enabled": settings.verify_enabled,
        "verify_timeout": settings.verify_timeout,
        "verify_mode": settings.verify_mode,
        "flood_enabled": settings.flood_enabled,
        "flood_max": settings.flood_max,
        "flood_window": settings.flood_window,
        "flood_mute": settings.flood_mute,
        "warn_threshold": settings.warn_threshold,
        "log_channel_id": settings.log_channel_id,
        "auto_reply_enabled": settings.auto_reply_enabled,
        "auto_replies": [
            {"keyword": item.keyword, "response": item.response, "mode": item.mode}
            for item in settings.auto_replies
        ],
        "announce_enabled": settings.announce_enabled,
        "announcements": [
            {
                "id": item.id,
                "text": item.text,
                "interval_minutes": item.interval_minutes,
                "enabled": item.enabled,
            }
            for item in settings.announcements
        ],
        "rules_enabled": settings.rules_enabled,
        "rules_text": settings.rules_text,
        "points_enabled": settings.points_enabled,
        "sign_daily": settings.sign_daily,
        "sign_bonus": list(settings.sign_bonus),
        "msg_per_point": settings.msg_per_point,
        "max_points_per_day": settings.max_points_per_day,
        "dice_cost": settings.dice_cost,
        "dice_reward": settings.dice_reward,
        "dice_reward_threshold": settings.dice_reward_threshold,
    }


def create_panel_app(state: PanelState) -> web.Application:
    expected_origin = _origin(state.cfg.panel_base_url) if state.cfg.panel_base_url else ""

    @web.middleware
    async def security_headers(request: web.Request, handler):
        response = await handler(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://telegram.org; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @web.middleware
    async def authenticate(request: web.Request, handler):
        if not request.path.startswith("/api/"):
            return await handler(request)
        peer = request.remote or "unknown"
        now = time.monotonic()
        window = float(state.cfg.panel_rate_window)
        if not state.limiter.allow(f"ip:{peer}", now, state.cfg.panel_rate_limit * 2, window):
            return _error(429, "请求太频繁，请稍后再试。")
        origin = request.headers.get("Origin")
        if origin and expected_origin and origin != expected_origin:
            return _error(403, "来源不被允许。")
        if request.method in {"POST", "PUT", "DELETE"}:
            content_type = request.content_type or ""
            if content_type != "application/json":
                return _error(415, "需要 JSON。")
        try:
            identity = validate_init_data(
                request.headers.get("X-Telegram-Init-Data", ""),
                state.cfg.token,
                max_age=state.cfg.panel_auth_max_age,
            )
        except AuthError as exc:
            return _error(401, exc.public)
        allowed = state.limiter.allow(
            f"user:{identity.user_id}",
            now,
            state.cfg.panel_rate_limit,
            window,
        )
        if not allowed:
            return _error(429, "请求太频繁，请稍后再试。")
        request[_IDENTITY] = identity
        return await handler(request)

    async def read_body(request: web.Request) -> tuple[dict | None, web.Response | None]:
        if request.content_length is not None and request.content_length > 65536:
            return None, _error(413, "请求过大。")
        try:
            payload = await request.json()
        except Exception:
            return None, _error(400, "JSON 无效。")
        if not isinstance(payload, dict):
            return None, _error(400, "JSON 必须是对象。")
        return payload, None

    async def load_group(request: web.Request):
        identity = request[_IDENTITY]
        try:
            chat_id = int(request.match_info["chat_id"])
        except ValueError:
            return None, _error(404, "找不到这个群。")
        role = await state.admin_cache.role(
            state.bot,
            chat_id,
            identity.user_id,
            state.cfg.admin_ids,
        )
        decision = group_access_decision(role)
        if decision == "unknown":
            return None, _error(403, "暂时无法确认你的管理员身份。")
        if decision:
            return None, _error(403, "你不是这个群的管理员。")
        row = await state.db.get_group(chat_id)
        if row is None:
            return None, _error(404, "机器人还没有见过这个群。")
        return row, None

    def current(row: dict):
        return resolve_settings(state.cfg, settings_dict(row), row.get("welcome_msg"))

    async def remember(chat_id: int, actor_id: int, action: str, detail: str) -> None:
        await state.db.log_action(
            chat_id=chat_id,
            user_id=None,
            actor_id=actor_id,
            kind="panel",
            action=action,
            detail=detail[:500],
            message_text=None,
            created_at=isoformat(utc_now()),
            max_length=state.cfg.message_max_length,
        )

    async def index(_request: web.Request) -> web.Response:
        return web.FileResponse(STATIC_DIR / "index.html")

    async def asset(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in {"app.js", "app.css"}:
            return _error(404, "找不到文件。")
        return web.FileResponse(STATIC_DIR / name)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def me(request: web.Request) -> web.Response:
        identity = request[_IDENTITY]
        return web.json_response(
            {
                "user_id": identity.user_id,
                "first_name": identity.first_name,
                "username": identity.username,
                "start_param": identity.start_param,
            }
        )

    async def groups(request: web.Request) -> web.Response:
        identity = request[_IDENTITY]
        rows = await state.db.list_groups()
        visible = []
        for row in rows[:100]:
            role = await state.admin_cache.role(
                state.bot,
                int(row["chat_id"]),
                identity.user_id,
                state.cfg.admin_ids,
            )
            if group_access_decision(role) is None:
                visible.append({"chat_id": row["chat_id"], "title": row["title"] or "未命名群组"})
        return web.json_response({"groups": visible})

    async def dashboard(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        stats = await state.db.group_stats(int(row["chat_id"]))
        member_count = None
        try:
            member_count = await state.bot.get_chat_member_count(int(row["chat_id"]))
        except Exception:
            logger.info("读取成员数失败 chat=%s", row["chat_id"])
        return web.json_response(
            {
                "chat_id": row["chat_id"],
                "title": row["title"] or "",
                "member_count": member_count,
                "tracked_members": stats["members"],
                "messages": stats["messages"],
                "actions": stats["actions"],
                "settings": _settings_view(current(row)),
            }
        )

    async def ranking(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        rows = await state.db.ranking(int(row["chat_id"]), 20)
        return web.json_response(
            {
                "ranking": [
                    {
                        "user_id": item["user_id"],
                        "name": item["first_name"] or item["username"] or f"用户{item['user_id']}",
                        "points": item["points"],
                        "messages": item["total_msgs"],
                    }
                    for item in rows
                ]
            }
        )

    async def warnings(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        rows = await state.db.list_warnings(int(row["chat_id"]))
        return web.json_response(
            {
                "warnings": [
                    {
                        "user_id": item["user_id"],
                        "warnings": item["warnings"],
                        "name": item["first_name"] or item["username"] or f"用户{item['user_id']}",
                    }
                    for item in rows
                ]
            }
        )

    async def log(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        scope = request.query.get("scope", "all")
        kinds = {"violations": VIOLATION_KINDS, "audit": AUDIT_KINDS}.get(scope)
        if scope not in {"all", "violations", "audit"}:
            return _error(400, "筛选范围不正确。")
        try:
            limit = min(max(int(request.query.get("limit", "30")), 1), 50)
        except ValueError:
            return _error(400, "数量不正确。")
        rows = await state.db.list_actions(int(row["chat_id"]), limit, kinds)
        zone = state.cfg.timezone()
        return web.json_response(
            {
                "entries": [
                    {
                        "id": item["id"],
                        "kind": item["kind"],
                        "action": item["action"],
                        "user_id": item["user_id"],
                        "actor_id": item["actor_id"],
                        "detail": item["detail"] or item["message_text"] or "",
                        "created_at": format_local(item["created_at"], zone),
                    }
                    for item in rows
                ]
            }
        )

    async def change_keywords(request: web.Request) -> web.Response:
        return await _mutate_list(request, "word", "keywords", mutate_keywords, state.cfg.keywords)

    async def change_patterns(request: web.Request) -> web.Response:
        return await _mutate_list(
            request,
            "pattern",
            "patterns",
            mutate_patterns,
            state.cfg.regex_patterns,
        )

    async def change_whitelist(request: web.Request) -> web.Response:
        return await _mutate_list(
            request,
            "domain",
            "whitelist",
            mutate_domains,
            state.cfg.whitelist_domains,
        )

    async def _mutate_list(request, field, action, mutator, base) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        body, bad = await read_body(request)
        if bad is not None or body is None:
            return bad or _error(400, "JSON 无效。")
        value = body.get(field)
        remove = body.get("remove", False)
        if not isinstance(value, str) or not isinstance(remove, bool):
            return _error(400, "参数不正确。")
        patch, error = mutator(settings_dict(row), base, value, remove=remove)
        if error:
            return _error(400, error)
        await state.db.patch_settings(int(row["chat_id"]), patch)
        await remember(int(row["chat_id"]), request[_IDENTITY].user_id, action, value[:80])
        updated = await state.db.get_group(int(row["chat_id"]))
        return web.json_response({"settings": _settings_view(current(updated or row))})

    async def change_flags(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        body, bad = await read_body(request)
        if bad is not None or body is None:
            return bad or _error(400, "JSON 无效。")
        patch = {}
        mode = body.pop("verify_mode", None)
        for key, value in body.items():
            if key not in BOOL_FIELDS or not isinstance(value, bool):
                return _error(400, "有无法识别的开关。")
            patch[key] = value
        if mode is not None:
            if mode not in {"button", "captcha"}:
                return _error(400, "验证方式只能是按钮或算术题。")
            patch["verify_mode"] = mode
        if not patch:
            return _error(400, "没有要修改的开关。")
        chat_id = int(row["chat_id"])
        await state.db.patch_settings(chat_id, patch)
        if "announce_enabled" in patch:
            await state.sync_announcements(chat_id)
        await remember(chat_id, request[_IDENTITY].user_id, "toggles", ",".join(patch))
        updated = await state.db.get_group(chat_id)
        return web.json_response({"settings": _settings_view(current(updated or row))})

    async def change_numbers(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        body, bad = await read_body(request)
        if bad is not None or body is None:
            return bad or _error(400, "JSON 无效。")
        patch = {}
        bonus = body.pop("sign_bonus", None)
        for key, value in body.items():
            bounds = NUMBER_FIELDS.get(key)
            if bounds is None or isinstance(value, bool) or not isinstance(value, int):
                return _error(400, "有无法识别的数字设置。")
            low, high = bounds
            if not low <= value <= high:
                return _error(400, f"{key} 超出范围。")
            patch[key] = value
        if bonus is not None:
            if (
                not isinstance(bonus, list)
                or not bonus
                or any(isinstance(item, bool) or not isinstance(item, int) for item in bonus)
                or any(item < 1 or item > 3650 for item in bonus)
            ):
                return _error(400, "连续签到奖励天数不正确。")
            patch["sign_bonus"] = bonus
        if not patch:
            return _error(400, "没有要修改的数字。")
        chat_id = int(row["chat_id"])
        await state.db.patch_settings(chat_id, patch)
        await remember(chat_id, request[_IDENTITY].user_id, "numbers", ",".join(patch))
        updated = await state.db.get_group(chat_id)
        return web.json_response({"settings": _settings_view(current(updated or row))})

    async def change_welcome(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        body, bad = await read_body(request)
        if bad is not None or body is None:
            return bad or _error(400, "JSON 无效。")
        chat_id = int(row["chat_id"])
        if "text" in body:
            text = body["text"]
            if text is None:
                await state.db.set_welcome(chat_id, None)
            elif isinstance(text, str) and len(text) <= 1000:
                await state.db.set_welcome(chat_id, text)
            else:
                return _error(400, "欢迎语需要不超过 1000 个字符。")
        if "buttons" in body:
            buttons, error = parse_buttons(body["buttons"])
            if error:
                return _error(400, error)
            await state.db.patch_settings(chat_id, {"welcome_buttons": buttons})
        await remember(chat_id, request[_IDENTITY].user_id, "welcome", "")
        updated = await state.db.get_group(chat_id)
        return web.json_response({"settings": _settings_view(current(updated or row))})

    async def change_replies(request: web.Request) -> web.Response:
        return await _replace_list(request, "replies", "auto_replies", parse_replies, False)

    async def change_announcements(request: web.Request) -> web.Response:
        return await _replace_list(
            request,
            "announcements",
            "announcements",
            parse_announcements,
            True,
        )

    async def _replace_list(request, field, key, parser, sync: bool) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        body, bad = await read_body(request)
        if bad is not None or body is None:
            return bad or _error(400, "JSON 无效。")
        items, error = parser(body.get(field))
        if error:
            return _error(400, error)
        chat_id = int(row["chat_id"])
        await state.db.patch_settings(chat_id, {key: items})
        if sync:
            await state.sync_announcements(chat_id)
        await remember(chat_id, request[_IDENTITY].user_id, key, str(len(items)))
        updated = await state.db.get_group(chat_id)
        return web.json_response({"settings": _settings_view(current(updated or row))})

    async def change_rules(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        body, bad = await read_body(request)
        if bad is not None or body is None:
            return bad or _error(400, "JSON 无效。")
        text = body.get("text", "")
        enabled = body.get("enabled", True)
        if not isinstance(text, str) or len(text) > 3500 or not isinstance(enabled, bool):
            return _error(400, "群规内容不正确。")
        chat_id = int(row["chat_id"])
        await state.db.patch_settings(chat_id, {"rules_text": text, "rules_enabled": enabled})
        await remember(chat_id, request[_IDENTITY].user_id, "rules", "")
        updated = await state.db.get_group(chat_id)
        return web.json_response({"settings": _settings_view(current(updated or row))})

    async def change_log_channel(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        body, bad = await read_body(request)
        if bad is not None or body is None:
            return bad or _error(400, "JSON 无效。")
        value = body.get("log_channel_id")
        if value in (None, 0):
            stored = 0
        elif isinstance(value, int) and not isinstance(value, bool):
            stored = value
        else:
            return _error(400, "日志频道 ID 不正确。")
        chat_id = int(row["chat_id"])
        await state.db.patch_settings(chat_id, {"log_channel_id": stored})
        await remember(chat_id, request[_IDENTITY].user_id, "log_channel", str(stored))
        updated = await state.db.get_group(chat_id)
        return web.json_response({"settings": _settings_view(current(updated or row))})

    async def test_message(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        body, bad = await read_body(request)
        if bad is not None or body is None:
            return bad or _error(400, "JSON 无效。")
        text = body.get("text")
        if not isinstance(text, str) or len(text) > 2000:
            return _error(400, "测试文本需要不超过 2000 个字符。")
        settings = current(row)
        verdict = scan(
            ScanInput(text),
            Policy(
                enabled=settings.anti_ad_enabled,
                keywords=settings.keywords,
                patterns=settings.regex_patterns,
                ban_links=settings.ban_links,
                whitelist_domains=settings.whitelist_domains,
                new_user_restrict_minutes=0,
                ban_channel_messages=settings.ban_channel_messages,
                ban_forwards=settings.ban_forwards,
            ),
        )
        reply = None
        if settings.auto_reply_enabled:
            from groupbot.groupsettings import match_reply

            reply = match_reply(text, settings.auto_replies)
        return web.json_response(
            {
                "violation": verdict.violation,
                "kind": verdict.kind,
                "detail": verdict.detail,
                "reply": reply,
            }
        )

    async def send_announcement(request: web.Request) -> web.Response:
        row, failure = await load_group(request)
        if failure is not None or row is None:
            return failure
        body, bad = await read_body(request)
        if bad is not None or body is None:
            return bad or _error(400, "JSON 无效。")
        ann_id = body.get("id")
        if not isinstance(ann_id, str):
            return _error(400, "缺少公告。")
        settings = current(row)
        text = next((item.text for item in settings.announcements if item.id == ann_id), "")
        if not text:
            return _error(404, "没有这条公告。")
        try:
            await state.bot.send_message(
                int(row["chat_id"]),
                text,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.warning("立即发送公告失败 chat=%s", row["chat_id"])
            return _error(502, "发送失败，请确认机器人还在群里。")
        await remember(int(row["chat_id"]), request[_IDENTITY].user_id, "announce_now", ann_id)
        return web.json_response({"ok": True})

    app = web.Application(
        middlewares=[security_headers, authenticate],
        client_max_size=65536,
    )
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/{name}", asset)
    app.router.add_get("/api/me", me)
    app.router.add_get("/api/groups", groups)
    app.router.add_get(r"/api/groups/{chat_id:-?\d+}", dashboard)
    app.router.add_get(r"/api/groups/{chat_id:-?\d+}/ranking", ranking)
    app.router.add_get(r"/api/groups/{chat_id:-?\d+}/warnings", warnings)
    app.router.add_get(r"/api/groups/{chat_id:-?\d+}/log", log)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/keywords", change_keywords)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/patterns", change_patterns)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/whitelist", change_whitelist)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/toggles", change_flags)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/numbers", change_numbers)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/welcome", change_welcome)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/replies", change_replies)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/announcements", change_announcements)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/rules", change_rules)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/log-channel", change_log_channel)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/test", test_message)
    app.router.add_post(r"/api/groups/{chat_id:-?\d+}/announcements/send", send_announcement)
    return app


async def start_panel(state: PanelState) -> web.AppRunner | None:
    if not state.cfg.panel_base_url:
        logger.info("未设置 PANEL_BASE_URL，管理面板不启动")
        return None
    runner = web.AppRunner(create_panel_app(state))
    await runner.setup()
    site = web.TCPSite(runner, state.cfg.panel_host, state.cfg.panel_port)
    await site.start()
    logger.info(
        "管理面板监听 %s:%s，对外地址 %s",
        state.cfg.panel_host,
        state.cfg.panel_port,
        state.cfg.panel_base_url,
    )
    return runner
