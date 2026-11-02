"""群管理员命令：禁言、警告、欢迎语、屏蔽词和设置。"""

import logging
from datetime import timedelta

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from groupbot.config import resolve_settings
from groupbot.helptext import SETTINGS_HELP
from groupbot.services import (
    Target,
    audit,
    block_reason,
    config,
    current_settings,
    database,
    delete_later,
    explain,
    mute_member,
    remember_group,
    require_admin,
    resolve_target,
    settings_dict,
    unmute_member,
)
from groupbot.textutil import (
    format_duration,
    html,
    mention,
    normalize_domain,
    parse_bool,
    parse_duration,
    render_template,
)
from groupbot.timeutil import clamp_mute_seconds, format_local, isoformat, mute_until, utc_now

logger = logging.getLogger(__name__)

_TOGGLES = {
    "anti_ad": "anti_ad_enabled",
    "links": "ban_links",
    "channel": "ban_channel_messages",
    "forwards": "ban_forwards",
    "verify": "verify_enabled",
    "service_delete": "delete_service",
    "leave_delete": "delete_leave",
    "flood": "flood_enabled",
    "welcome": "welcome_enabled",
    "reply": "auto_reply_enabled",
    "announce": "announce_enabled",
    "rules": "rules_enabled",
    "points": "points_enabled",
}

_NUMBERS = {
    "restrict": ("new_user_restrict_minutes", 0, 10080),
    "mute": ("mute_duration", 0, 366 * 24 * 3600),
    "warns": ("warn_threshold", 1, 20),
    "timeout": ("verify_timeout", 15, 3600),
    "flood_limit": ("flood_max", 2, 50),
    "flood_window": ("flood_window", 2, 300),
    "flood_mute": ("flood_mute", 30, 86400),
}

_RESET_WELCOME = {"-", "reset", "默认", "清空"}


def _actor(update) -> tuple[int | None, str]:
    user = update.effective_user
    if user is None:
        return None, "管理员"
    return user.id, mention(user.id, user.full_name or user.first_name or "管理员")


def _strings(overrides: dict, key: str) -> list[str]:
    value = overrides.get(key, [])
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


async def _ready(update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return None
    await remember_group(update, context)
    return update.effective_message


async def ban_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    target, rest = await resolve_target(update, context)
    if target is None:
        return
    reason = await block_reason(update, context, target, allow_absent=False)
    if reason:
        await message.reply_text(reason)
        return
    duration = parse_duration(rest[0]) if rest else None
    if duration is not None:
        rest = rest[1:]
    else:
        duration = timedelta(minutes=60)
    why = " ".join(rest).strip() or "违反群规"
    actual, note = clamp_mute_seconds(int(duration.total_seconds()))
    try:
        await mute_member(context, chat.id, target.user_id, mute_until(utc_now(), actual))
    except TelegramError as exc:
        logger.exception("禁言失败")
        await message.reply_text(explain(exc))
        return
    _actor_id, actor = _actor(update)
    shown = format_duration(actual)
    await message.reply_text(
        "🚫 <b>已禁言</b>\n\n"
        f"用户：{mention(target.user_id, target.name)}\n"
        f"时长：{html(shown)}{html(note)}\n"
        f"原因：{html(why)}\n"
        f"操作者：{actor}",
        parse_mode=ParseMode.HTML,
    )
    await audit(
        context,
        chat_id=chat.id,
        user_id=target.user_id,
        actor_id=_actor_id,
        kind="ban",
        action="mute",
        detail=f"{shown} {why}",
    )


async def unban_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    target = await _target_or_stop(update, context, message, allow_absent=True)
    if target is None:
        return
    try:
        await unmute_member(context, chat.id, target.user_id)
        await context.bot.unban_chat_member(chat.id, target.user_id, only_if_banned=True)
    except TelegramError as exc:
        logger.exception("解除禁言失败")
        await message.reply_text(explain(exc))
        return
    actor_id, _actor_label = _actor(update)
    await message.reply_text(
        f"✅ 已解除 {mention(target.user_id, target.name)} 的禁言。",
        parse_mode=ParseMode.HTML,
    )
    await audit(
        context,
        chat_id=chat.id,
        user_id=target.user_id,
        actor_id=actor_id,
        kind="unban",
        action="unmute",
    )


async def kick_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    target, rest = await resolve_target(update, context)
    if target is None:
        return
    reason = await block_reason(update, context, target, allow_absent=False)
    if reason:
        await message.reply_text(reason)
        return
    why = " ".join(rest).strip() or "违反群规"
    try:
        await context.bot.ban_chat_member(chat.id, target.user_id)
        await context.bot.unban_chat_member(chat.id, target.user_id)
    except TelegramError as exc:
        logger.exception("踢出失败")
        await message.reply_text(explain(exc))
        return
    actor_id, actor = _actor(update)
    await message.reply_text(
        f"👢 已将 {mention(target.user_id, target.name)} 移出群组。\n"
        f"原因：{html(why)}\n"
        f"操作者：{actor}",
        parse_mode=ParseMode.HTML,
    )
    await audit(
        context,
        chat_id=chat.id,
        user_id=target.user_id,
        actor_id=actor_id,
        kind="kick",
        action="kick",
        detail=why,
    )


async def warn_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    target, rest = await resolve_target(update, context)
    if target is None:
        return
    reason = await block_reason(update, context, target, allow_absent=False)
    if reason:
        await message.reply_text(reason)
        return
    why = " ".join(rest).strip() or "违反群规"
    settings = await current_settings(context, chat)
    store = database(context)
    member = await store.ensure_member(
        chat.id,
        target.user_id,
        target.username,
        target.name,
        isoformat(utc_now()),
    )
    count = int(member["warnings"]) + 1
    actor_id, actor = _actor(update)
    reached = count >= settings.warn_threshold
    if reached:
        actual, note = clamp_mute_seconds(settings.mute_duration)
        try:
            await mute_member(context, chat.id, target.user_id, mute_until(utc_now(), actual))
        except TelegramError as exc:
            logger.exception("警告达上限但禁言失败")
            await store.set_warnings(chat.id, target.user_id, count)
            await message.reply_text(explain(exc))
            return
        await store.set_warnings(chat.id, target.user_id, 0)
        outcome = (
            f"已达 {settings.warn_threshold} 次，"
            f"已禁言 {format_duration(actual)}{note}，警告已清零。"
        )
    else:
        await store.set_warnings(chat.id, target.user_id, count)
        left = settings.warn_threshold - count
        outcome = f"警告 {count}/{settings.warn_threshold}，再警告 {left} 次将被禁言。"
    await message.reply_text(
        "⚠️ <b>警告</b>\n\n"
        f"用户：{mention(target.user_id, target.name)}\n"
        f"原因：{html(why)}\n"
        f"{html(outcome)}\n"
        f"操作者：{actor}",
        parse_mode=ParseMode.HTML,
    )
    await audit(
        context,
        chat_id=chat.id,
        user_id=target.user_id,
        actor_id=actor_id,
        kind="warn",
        action="mute" if reached else "warn",
        detail=why,
    )


async def unwarn_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    target, rest = await resolve_target(update, context)
    if target is None:
        return
    reason = await block_reason(update, context, target, allow_absent=True)
    if reason:
        await message.reply_text(reason)
        return
    store = database(context)
    member = await store.get_member(chat.id, target.user_id)
    current = int(member["warnings"]) if member else 0
    if rest and rest[0].lower() in {"all", "清零"}:
        updated = 0
    elif rest and rest[0].isdigit():
        updated = int(rest[0])
    else:
        updated = max(0, current - 1)
    if member is not None:
        updated = await store.set_warnings(chat.id, target.user_id, updated)
    actor_id, _label = _actor(update)
    await message.reply_text(
        f"已将 {mention(target.user_id, target.name)} 的警告调整为 {updated} 次。",
        parse_mode=ParseMode.HTML,
    )
    await audit(
        context,
        chat_id=chat.id,
        user_id=target.user_id,
        actor_id=actor_id,
        kind="unwarn",
        action="unwarn",
        detail=str(updated),
    )


async def warns_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    target = await _target_or_stop(update, context, message, allow_absent=True)
    if target is None:
        return
    member = await database(context).get_member(chat.id, target.user_id)
    count = int(member["warnings"]) if member else 0
    settings = await current_settings(context, chat)
    await message.reply_text(
        f"{mention(target.user_id, target.name)} 当前警告 {count}/{settings.warn_threshold}。",
        parse_mode=ParseMode.HTML,
    )


async def setwelcome_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    body = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        body = parts[1].strip() if len(parts) > 1 else ""
    settings = await current_settings(context, chat)
    if not body:
        preview = render_template(
            settings.welcome_message,
            name="示例",
            username="example",
            id="1",
            chat=chat.title or "",
        )
        await message.reply_text(
            f"当前欢迎语：\n{html(settings.welcome_message)}\n\n预览：\n{preview}\n\n"
            "用 /setwelcome 文本 修改，/setwelcome 默认 恢复全局欢迎语。",
            parse_mode=ParseMode.HTML,
        )
        return
    if body in _RESET_WELCOME:
        await database(context).set_welcome(chat.id, None)
        await message.reply_text("已恢复为全局欢迎语。")
    elif len(body) > 1000:
        await message.reply_text("欢迎语不能超过 1000 个字符。")
        return
    else:
        await database(context).set_welcome(chat.id, body)
        await message.reply_text("已更新欢迎语。")
    actor_id, _label = _actor(update)
    await audit(
        context,
        chat_id=chat.id,
        user_id=None,
        actor_id=actor_id,
        kind="settings",
        action="welcome",
    )


async def addword_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    word = " ".join(context.args or []).strip()
    if not word or len(word) > 32:
        await message.reply_text("用法：/addword 关键词（1 到 32 个字符）")
        return
    overrides = await _overrides(context, chat.id)
    extra = _strings(overrides, "extra_keywords")
    disabled = _strings(overrides, "disabled_keywords")
    folded = word.casefold()
    settings = await current_settings(context, chat)
    if any(item.casefold() == folded for item in settings.keywords):
        await message.reply_text("这个词已经在列表里。")
        return
    disabled = [item for item in disabled if item.casefold() != folded]
    known = {item.casefold() for item in (*config(context).keywords, *extra)}
    if folded not in known:
        if len(extra) >= 200:
            await message.reply_text("本群额外屏蔽词已达 200 个上限。")
            return
        extra.append(word)
    await database(context).patch_settings(
        chat.id,
        {"extra_keywords": extra, "disabled_keywords": disabled},
    )
    await message.reply_text(f"已添加屏蔽词：{word}")
    actor_id, _label = _actor(update)
    await audit(
        context,
        chat_id=chat.id,
        user_id=None,
        actor_id=actor_id,
        kind="settings",
        action="addword",
        detail=word,
    )


async def delword_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    word = " ".join(context.args or []).strip()
    if not word:
        await message.reply_text("用法：/delword 关键词")
        return
    settings = await current_settings(context, chat)
    folded = word.casefold()
    if not any(item.casefold() == folded for item in settings.keywords):
        await message.reply_text("没有这个屏蔽词。")
        return
    overrides = await _overrides(context, chat.id)
    extra = [item for item in _strings(overrides, "extra_keywords") if item.casefold() != folded]
    disabled = _strings(overrides, "disabled_keywords")
    if any(item.casefold() == folded for item in config(context).keywords):
        if folded not in {item.casefold() for item in disabled}:
            disabled.append(word)
    await database(context).patch_settings(
        chat.id,
        {"extra_keywords": extra, "disabled_keywords": disabled},
    )
    await message.reply_text(f"已删除屏蔽词：{word}")
    actor_id, _label = _actor(update)
    await audit(
        context,
        chat_id=chat.id,
        user_id=None,
        actor_id=actor_id,
        kind="settings",
        action="delword",
        detail=word,
    )


async def wordlist_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    settings = await current_settings(context, chat)
    if not settings.keywords:
        await message.reply_text("当前没有屏蔽词。")
        return
    lines = ["<b>当前屏蔽词</b>", ""]
    lines.extend(f"• {html(word)}" for word in settings.keywords)
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def settings_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    overrides = await _overrides(context, chat.id)
    settings = resolve_settings(
        config(context),
        overrides,
        (await database(context).get_group(chat.id) or {}).get("welcome_msg"),
    )
    args = [str(arg) for arg in (context.args or [])]
    if not args:
        text = _format_settings(settings, overrides) + "\n" + SETTINGS_HELP
        await message.reply_text(text, parse_mode=ParseMode.HTML)
        return
    key = args[0].lower()
    changed = await _apply_setting(context, chat.id, key, args[1:], overrides, settings)
    if changed is None:
        await message.reply_text("没有这个设置项。发送 /settings 查看用法。")
        return
    await message.reply_text(changed)
    if changed.startswith(("请", "用法", "超出", "域名")):
        return
    if key == "announce":
        from groupbot.announce import sync_announcement_jobs

        await sync_announcement_jobs(context.application, chat.id)
    actor_id, _label = _actor(update)
    await audit(
        context,
        chat_id=chat.id,
        user_id=None,
        actor_id=actor_id,
        kind="settings",
        action=key,
        detail=" ".join(args[1:])[:200],
    )


async def stats_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    try:
        members = await context.bot.get_chat_member_count(chat.id)
    except TelegramError:
        members = "未知"
        logger.warning("读取成员数失败", exc_info=True)
    data = await database(context).group_stats(chat.id)
    await message.reply_text(
        "📊 <b>群组统计</b>\n\n"
        f"群组：{html(chat.title or '')}\n"
        f"成员数：{members}\n"
        f"已记录成员：{data['members']}\n"
        f"消息记录：{data['messages']}\n"
        f"管理记录：{data['actions']}\n"
        f"ID：<code>{chat.id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def clean_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    try:
        count = int(context.args[0]) if context.args else 10
    except ValueError:
        await message.reply_text("用法：/clean 数量")
        return
    if count < 1:
        await message.reply_text("数量至少为 1。")
        return
    count = min(count, 100)
    ids = [message.message_id - offset for offset in range(1, count + 1)]
    ids = [message_id for message_id in ids if message_id > 0]
    if not ids:
        await message.reply_text("没有更早的消息可以清理。")
        return
    try:
        await context.bot.delete_messages(chat.id, ids)
    except TelegramError as exc:
        logger.exception("清理消息失败")
        await message.reply_text(explain(exc))
        return
    notice = await message.reply_text(
        f"已提交清理 {len(ids)} 条消息。超出 48 小时或无权删除的会跳过。"
    )
    await delete_later(context, chat.id, notice.message_id, 5)
    await delete_later(context, chat.id, message.message_id, 5)
    actor_id, _label = _actor(update)
    await audit(
        context,
        chat_id=chat.id,
        user_id=None,
        actor_id=actor_id,
        kind="clean",
        action="delete",
        detail=f"{len(ids)} 条",
    )


async def violations_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ready(update, context)
    chat = update.effective_chat
    if message is None or chat is None:
        return
    limit = 10
    if context.args:
        if not str(context.args[0]).isdigit():
            await message.reply_text("用法：/violations [数量]")
            return
        limit = min(max(int(context.args[0]), 1), 30)
    rows = await database(context).list_actions(chat.id, limit)
    if not rows:
        await message.reply_text("还没有记录。")
        return
    tz = config(context).timezone()
    lines = ["📋 <b>最近记录</b>", ""]
    for row in rows:
        snippet = row["message_text"] or row["detail"] or ""
        lines.append(
            f"#{row['id']} {html(row['kind'])} / {html(row['action'])} "
            f"用户 {row['user_id'] or '—'}\n"
            f"{html(format_local(row['created_at'], tz))} {html(snippet[:80])}"
        )
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "…"
    await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def _target_or_stop(update, context, message, *, allow_absent: bool) -> Target | None:
    target, _rest = await resolve_target(update, context)
    if target is None:
        return None
    reason = await block_reason(update, context, target, allow_absent=allow_absent)
    if reason:
        await message.reply_text(reason)
        return None
    return target


async def _overrides(context, chat_id: int) -> dict:
    row = await database(context).get_group(chat_id)
    return settings_dict(row)


def _format_settings(settings, overrides: dict) -> str:
    if "log_channel_id" in overrides and settings.log_channel_id is None:
        log_text = "本群已关闭"
    elif settings.log_channel_id:
        log_text = str(settings.log_channel_id)
    else:
        log_text = "未设置"
    domains = "、".join(settings.whitelist_domains[:12]) or "（空）"
    if len(settings.whitelist_domains) > 12:
        domains += "…"
    mode = "算术" if settings.verify_mode == "captcha" else "按钮"
    return (
        "⚙️ <b>当前群设置</b>\n\n"
        f"广告过滤：{_on(settings.anti_ad_enabled)}\n"
        f"拦截链接：{_on(settings.ban_links)}\n"
        f"频道身份：{_on(settings.ban_channel_messages)}\n"
        f"转发：{_on(settings.ban_forwards)}\n"
        f"入群验证：{_on(settings.verify_enabled)}（{mode}，{settings.verify_timeout} 秒）\n"
        f"删除进群/退群提示：{_on(settings.delete_service)} / {_on(settings.delete_leave)}\n"
        f"刷屏：{_on(settings.flood_enabled)}（{settings.flood_window} 秒 "
        f"{settings.flood_max} 条，禁言 {settings.flood_mute} 秒）\n"
        f"新成员限制链接：{settings.new_user_restrict_minutes} 分钟\n"
        f"自动禁言：{html(format_duration(settings.mute_duration))}\n"
        f"警告上限：{settings.warn_threshold}\n"
        f"发言积分：每 {settings.msg_per_point} 条 1 分，每天最多 {settings.max_points_per_day}\n"
        f"签到：{settings.sign_daily} 分\n"
        f"日志频道：{html(log_text)}\n"
        f"链接白名单：{html(domains)}\n"
        f"屏蔽词：{len(settings.keywords)} 个\n"
        f"欢迎/回复/公告/群规/积分："
        f"{_on(settings.welcome_enabled)}/"
        f"{_on(settings.auto_reply_enabled)}/"
        f"{_on(settings.announce_enabled)}/"
        f"{_on(settings.rules_enabled)}/"
        f"{_on(settings.points_enabled)}\n"
    )


def _on(value: bool) -> str:
    return "开" if value else "关"


async def _apply_setting(
    context,
    chat_id: int,
    key: str,
    rest: list[str],
    overrides: dict,
    settings,
) -> str | None:
    if key in _TOGGLES:
        return await _set_toggle(context, chat_id, key, rest)
    if key == "captcha":
        return await _set_captcha(context, chat_id, rest)
    if key in _NUMBERS:
        return await _set_number(context, chat_id, key, rest)
    if key == "whitelist":
        return await _set_whitelist(context, chat_id, rest, overrides, settings)
    if key == "log_channel":
        return await _set_log_channel(context, chat_id, rest)
    return None


async def _set_toggle(context, chat_id: int, key: str, rest: list[str]) -> str:
    if len(rest) != 1 or parse_bool(rest[0]) is None:
        return "请用 开 或 关。"
    value = bool(parse_bool(rest[0]))
    await database(context).patch_settings(chat_id, {_TOGGLES[key]: value})
    return f"已将 {key} 设为 {_on(value)}。"


async def _set_captcha(context, chat_id: int, rest: list[str]) -> str:
    if len(rest) != 1 or parse_bool(rest[0]) is None:
        return "请用 开 或 关。"
    enabled = bool(parse_bool(rest[0]))
    patch: dict = {"verify_mode": "captcha" if enabled else "button"}
    if enabled:
        patch["verify_enabled"] = True
    await database(context).patch_settings(chat_id, patch)
    return "已改为算术验证。" if enabled else "已改为按钮验证。"


async def _set_number(context, chat_id: int, key: str, rest: list[str]) -> str:
    field, low, high = _NUMBERS[key]
    if len(rest) != 1:
        return f"请提供 {low} 到 {high} 的整数。"
    try:
        number = int(rest[0])
    except ValueError:
        return f"请提供 {low} 到 {high} 的整数。"
    if not low <= number <= high:
        return f"超出范围，请填 {low} 到 {high}。"
    await database(context).patch_settings(chat_id, {field: number})
    return f"已将 {key} 设为 {number}。"


async def _set_whitelist(context, chat_id: int, rest: list[str], overrides: dict, settings) -> str:
    if len(rest) != 2 or rest[0].lower() not in {"add", "del"}:
        return "用法：/settings whitelist add 域名"
    domain = normalize_domain(rest[1])
    if domain is None:
        return "域名格式不正确。"
    extra = _strings(overrides, "extra_whitelist")
    disabled = _strings(overrides, "disabled_whitelist")

    def folded(items: list[str]) -> set[str]:
        return {(normalize_domain(item) or item.lower()) for item in items}

    if rest[0].lower() == "add":
        disabled = [item for item in disabled if (normalize_domain(item) or item.lower()) != domain]
        known = folded(list(settings.whitelist_domains)) | folded(extra)
        if domain not in known:
            extra.append(domain)
        action = "加入白名单"
    else:
        extra = [item for item in extra if (normalize_domain(item) or item.lower()) != domain]
        base = folded(list(config(context).whitelist_domains))
        if domain in base and domain not in folded(disabled):
            disabled.append(domain)
        action = "移出白名单"
    await database(context).patch_settings(
        chat_id,
        {"extra_whitelist": extra, "disabled_whitelist": disabled},
    )
    return f"已将 {domain} {action}。"


async def _set_log_channel(context, chat_id: int, rest: list[str]) -> str:
    if len(rest) != 1:
        return "用法：/settings log_channel 频道ID 或 关"
    token = rest[0]
    if token.lower() in {"off", "关"} or parse_bool(token) is False:
        await database(context).patch_settings(chat_id, {"log_channel_id": 0})
        return "本群不再转发管理日志。"
    try:
        channel_id = int(token)
    except ValueError:
        return "请提供数字 ID，或写 关。"
    await database(context).patch_settings(chat_id, {"log_channel_id": channel_id})
    return f"管理日志将转发到 {channel_id}。"
