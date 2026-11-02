"""广告过滤、刷屏限制、入群验证，以及进群退群提示的延迟删除。"""

import asyncio
import logging
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes, filters

from groupbot.groupsettings import match_reply
from groupbot.moderation import (
    KIND_FLOOD,
    FloodTracker,
    Policy,
    TextEntity,
    scan_parts,
)
from groupbot.services import (
    admin_cache,
    audit,
    config,
    current_settings,
    database,
    delete_later,
    is_group,
    mute_member,
    remember_group,
    unmute_member,
)
from groupbot.textutil import html, mention, render_template
from groupbot.timeutil import isoformat, local_today, mute_until, parse_iso, utc_now
from groupbot.verify import Challenge, callback_data, make_challenge, parse_callback_data

logger = logging.getLogger(__name__)

PUBLIC = {
    "keyword": "这条消息里有屏蔽词。",
    "regex": "这条消息里有违规内容。",
    "link": "不能发送未允许的链接。",
    "new_user_link": "新成员暂时不能发送链接。",
    "channel": "不能用频道身份发言。",
    "forward": "不能转发消息。",
    "flood": "发送太快了，先停一下。",
}

CONTENT_FILTER = (
    filters.TEXT
    | filters.CAPTION
    | filters.PHOTO
    | filters.VIDEO
    | filters.ANIMATION
    | filters.Document.ALL
    | filters.AUDIO
    | filters.VOICE
    | filters.VIDEO_NOTE
    | filters.Sticker.ALL
    | filters.CONTACT
    | filters.LOCATION
    | filters.POLL
    | filters.FORWARDED
) & ~filters.COMMAND & ~filters.StatusUpdate.ALL


def verify_job_name(chat_id: int, user_id: int) -> str:
    return f"verify:{chat_id}:{user_id}"


def _verify_lock(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> asyncio.Lock:
    locks = context.application.bot_data.setdefault("verify_locks", {})
    lock = locks.get((chat_id, user_id))
    if lock is None:
        lock = asyncio.Lock()
        locks[(chat_id, user_id)] = lock
    return lock


def _entities(raw) -> tuple[TextEntity, ...]:
    if not raw:
        return ()
    return tuple(
        TextEntity(
            type=entity.type,
            offset=entity.offset,
            length=entity.length,
            url=getattr(entity, "url", None),
        )
        for entity in raw
    )


def _policy(settings) -> Policy:
    return Policy(
        enabled=settings.anti_ad_enabled,
        keywords=settings.keywords,
        patterns=settings.regex_patterns,
        ban_links=settings.ban_links,
        whitelist_domains=settings.whitelist_domains,
        new_user_restrict_minutes=settings.new_user_restrict_minutes,
        ban_channel_messages=settings.ban_channel_messages,
        ban_forwards=settings.ban_forwards,
    )


def _extra_rows(settings) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for item in settings.welcome_buttons:
        if item.action == "rules":
            rows.append([InlineKeyboardButton(item.text, callback_data="rules")])
        elif item.url:
            rows.append([InlineKeyboardButton(item.text, url=item.url)])
    return rows


def _with_extra(markup: InlineKeyboardMarkup | None, settings) -> InlineKeyboardMarkup | None:
    rows = list(markup.inline_keyboard) if markup is not None else []
    rows.extend(_extra_rows(settings))
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def _markup(user_id: int, challenge: Challenge) -> InlineKeyboardMarkup:
    if challenge.answer == "ok":
        button = InlineKeyboardButton("✅ 我不是机器人", callback_data=callback_data(user_id, "ok"))
        return InlineKeyboardMarkup([[button]])
    buttons = [
        InlineKeyboardButton(choice, callback_data=callback_data(user_id, choice))
        for choice in challenge.choices
    ]
    return InlineKeyboardMarkup([buttons])


async def _welcome_text(settings, member, chat_title: str) -> str:
    return render_template(
        settings.welcome_message,
        name=member.full_name or member.first_name or "新成员",
        username=member.username or "",
        id=str(member.id),
        chat=chat_title,
    )


async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.new_chat_members:
        return
    await remember_group(update, context)
    settings = await current_settings(context, chat)
    for member in message.new_chat_members:
        if member.is_bot:
            if member.id == context.bot.id:
                await message.reply_text(
                    "我已进群。请把我设为管理员（删除消息、封禁用户），"
                    "并在 BotFather 里对这个机器人关闭 Privacy Mode。"
                )
            continue
        await database(context).ensure_member(
            chat.id,
            member.id,
            member.username,
            member.first_name or "用户",
            isoformat(utc_now()),
            joined=True,
        )
        role = await admin_cache(context).role(
            context.bot, chat.id, member.id, config(context).admin_ids
        )
        welcome = await _welcome_text(settings, member, chat.title or "")
        if settings.verify_enabled and role == "member":
            await _start_verification(context, message, chat.id, member.id, welcome, settings)
        elif settings.welcome_enabled or settings.welcome_buttons:
            await message.reply_text(
                welcome if settings.welcome_enabled else "欢迎加入。",
                parse_mode=ParseMode.HTML,
                reply_markup=_with_extra(None, settings),
            )
    if settings.delete_service:
        await delete_later(context, chat.id, message.message_id, settings.delete_delay)


async def _start_verification(context, message, chat_id, user_id, welcome: str, settings) -> None:
    try:
        await mute_member(context, chat_id, user_id, None)
    except TelegramError:
        logger.warning("入群禁言失败，改为只发送欢迎语 chat=%s user=%s", chat_id, user_id)
        await message.reply_text(
            welcome if settings.welcome_enabled else "请先完成入群验证。",
            parse_mode=ParseMode.HTML,
            reply_markup=_with_extra(None, settings),
        )
        return
    challenge = make_challenge(settings.verify_mode)
    base = welcome if settings.welcome_enabled else "请先完成入群验证。"
    prompt = f"{base}\n\n请在 {settings.verify_timeout} 秒内完成验证，超时会被移出群组。"
    if challenge.prompt:
        prompt += f"\n{html(challenge.prompt)} = ?"
    sent = await message.reply_text(
        prompt,
        parse_mode=ParseMode.HTML,
        reply_markup=_with_extra(_markup(user_id, challenge), settings),
    )
    deadline = utc_now() + timedelta(seconds=settings.verify_timeout)
    await database(context).save_pending(
        chat_id,
        user_id,
        sent.message_id,
        challenge.answer,
        isoformat(deadline),
    )
    if context.job_queue is None:
        logger.error("任务队列不可用，无法在验证超时后踢人")
        return
    context.job_queue.run_once(
        verification_timeout,
        when=settings.verify_timeout,
        data={"chat_id": chat_id, "user_id": user_id},
        name=verify_job_name(chat_id, user_id),
    )


def _cancel_verify_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    if context.job_queue is None:
        return
    for job in context.job_queue.get_jobs_by_name(verify_job_name(chat_id, user_id)):
        job.schedule_removal()


async def verification_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data if context.job is not None else None
    if not isinstance(data, dict):
        return
    chat_id = int(data["chat_id"])
    user_id = int(data["user_id"])
    async with _verify_lock(context, chat_id, user_id):
        pending = await database(context).get_pending(chat_id, user_id)
        if pending is None:
            return
        await database(context).delete_pending(chat_id, user_id)
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await context.bot.unban_chat_member(chat_id, user_id)
    except TelegramError:
        logger.exception("验证超时后踢人失败 chat=%s user=%s", chat_id, user_id)
    message_id = pending.get("message_id")
    if message_id:
        await delete_later(context, chat_id, int(message_id), 0)
    await audit(
        context,
        chat_id=chat_id,
        user_id=user_id,
        actor_id=None,
        kind="verify_kick",
        action="kick",
        detail="入群验证超时",
    )


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    parsed = parse_callback_data(query.data)
    if parsed is None:
        return
    user_id, choice = parsed
    message = query.message
    chat = message.chat if message is not None else update.effective_chat
    if chat is None:
        return
    if query.from_user.id != user_id:
        await query.answer("这不是你的验证。", show_alert=True)
        return
    async with _verify_lock(context, chat.id, user_id):
        pending = await database(context).get_pending(chat.id, user_id)
        if pending is None:
            await query.answer("验证已结束。", show_alert=True)
            return
        if choice != pending["answer"]:
            await query.answer("答案不对，请再试一次。", show_alert=True)
            return
        try:
            await unmute_member(context, chat.id, user_id)
        except TelegramError:
            logger.exception("验证通过但解除禁言失败")
            await query.answer("验证通过，但解除禁言失败，请联系管理员。", show_alert=True)
            return
        await database(context).delete_pending(chat.id, user_id)
        _cancel_verify_job(context, chat.id, user_id)
    await query.answer("验证通过")
    if message is not None:
        try:
            await query.edit_message_text("✅ 验证通过，欢迎加入！")
        except TelegramError:
            logger.debug("编辑验证消息失败", exc_info=True)
        await delete_later(context, chat.id, message.message_id, 10)


async def resume_pending_verifications(app: Application) -> None:
    if app.job_queue is None:
        return
    rows = await app.bot_data["db"].list_pending()
    now = utc_now()
    for row in rows:
        delay = (parse_iso(row["deadline"]) - now).total_seconds()
        app.job_queue.run_once(
            verification_timeout,
            when=max(delay, 1),
            data={"chat_id": row["chat_id"], "user_id": row["user_id"]},
            name=verify_job_name(row["chat_id"], row["user_id"]),
        )


async def rules_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    settings = await current_settings(context, query.message.chat)
    if not settings.rules_enabled or not settings.rules_text:
        await query.answer("本群还没有群规。", show_alert=True)
        return
    await query.answer()
    await query.message.reply_text(settings.rules_text)


async def member_left(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    settings = await current_settings(context, chat)
    if settings.delete_leave:
        await delete_later(context, chat.id, message.message_id, settings.delete_delay)
    left = message.left_chat_member
    if left is None:
        return
    await database(context).delete_pending(chat.id, left.id)
    _cancel_verify_job(context, chat.id, left.id)


async def moderate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or not is_group(chat) or chat is None:
        return
    if message.sender_chat is not None and message.sender_chat.id == chat.id:
        return
    user = message.from_user
    if user is not None and user.id == context.bot.id:
        return
    await remember_group(update, context)
    settings = await current_settings(context, chat)
    if user is not None:
        role = await admin_cache(context).role(
            context.bot, chat.id, user.id, config(context).admin_ids
        )
        if role == "admin":
            await database(context).ensure_member(
                chat.id,
                user.id,
                user.username,
                user.first_name or "用户",
                isoformat(utc_now()),
            )
            await _award(context, chat.id, user, message.text or message.caption or "", settings)
            return
        if role == "unknown":
            return
        await database(context).ensure_member(
            chat.id,
            user.id,
            user.username,
            user.first_name or "用户",
            isoformat(utc_now()),
        )
        if settings.flood_enabled and _flooding(context, chat.id, user.id, settings):
            await _punish(
                context,
                chat.id,
                message,
                user,
                KIND_FLOOD,
                "刷屏",
                message.text or message.caption,
                settings,
                mute_seconds=settings.flood_mute,
            )
            return

    member = None
    if user is not None:
        member = await database(context).get_member(chat.id, user.id)
    joined = parse_iso(member["join_date"]) if member and member.get("join_date") else None
    sender = message.sender_chat
    verdict = scan_parts(
        text=message.text,
        caption=message.caption,
        entities=_entities(message.entities),
        caption_entities=_entities(message.caption_entities),
        policy=_policy(settings),
        sender_chat_id=sender.id if sender is not None else None,
        sender_chat_type=sender.type if sender is not None else None,
        chat_id=chat.id,
        is_automatic_forward=bool(message.is_automatic_forward),
        is_forward=message.forward_origin is not None,
        joined_at=joined,
        now=utc_now(),
    )
    if verdict.violation and verdict.kind is not None:
        await _punish(
            context,
            chat.id,
            message,
            user,
            verdict.kind,
            verdict.detail,
            message.text or message.caption,
            settings,
            mute_seconds=None,
        )
        return
    body = message.text or message.caption or ""
    if settings.auto_reply_enabled and body:
        reply = match_reply(body, settings.auto_replies)
        if reply:
            try:
                await message.reply_text(reply)
            except TelegramError:
                logger.info("自动回复发送失败 chat=%s", chat.id)
    if user is not None:
        await _award(context, chat.id, user, body, settings)


def _flooding(context, chat_id: int, user_id: int, settings) -> bool:
    tracker: FloodTracker = context.application.bot_data["flood"]
    return tracker.is_flood(
        chat_id,
        user_id,
        time.monotonic(),
        settings.flood_window,
        settings.flood_max,
    )


async def _award(context, chat_id: int, user, text: str, settings) -> None:
    if not text.strip():
        return
    today = local_today(utc_now(), ZoneInfo(settings.timezone_name))
    await database(context).record_message(
        chat_id,
        user.id,
        text,
        today,
        settings.msg_per_point if settings.points_enabled else 0,
        settings.max_points_per_day,
        isoformat(utc_now()),
        settings.message_max_length,
    )


async def _punish(
    context,
    chat_id: int,
    message,
    user,
    kind: str,
    detail: str | None,
    message_text: str | None,
    settings,
    *,
    mute_seconds: int | None,
) -> None:
    try:
        await message.delete()
    except TelegramError:
        logger.warning("删除违规消息失败 chat=%s", chat_id)
        await audit(
            context,
            chat_id=chat_id,
            user_id=user.id if user is not None else None,
            actor_id=None,
            kind=kind,
            action="delete_failed",
            detail=detail,
            message_text=message_text,
        )
        return
    if user is not None:
        who = mention(user.id, user.full_name or user.first_name or "用户")
    else:
        who = "有一条消息"
    notice = await context.bot.send_message(
        chat_id,
        f"🚫 {who}，{PUBLIC.get(kind, '这条消息违规了。')}",
        parse_mode=ParseMode.HTML,
    )
    await delete_later(context, chat_id, notice.message_id, settings.warning_delete_delay)
    if mute_seconds is not None and user is not None:
        try:
            await mute_member(context, chat_id, user.id, mute_until(utc_now(), mute_seconds))
        except TelegramError:
            logger.warning("刷屏禁言失败 chat=%s user=%s", chat_id, user.id)
    await audit(
        context,
        chat_id=chat_id,
        user_id=user.id if user is not None else None,
        actor_id=None,
        kind=kind,
        action="mute" if mute_seconds is not None else "delete",
        detail=detail,
        message_text=message_text,
    )
