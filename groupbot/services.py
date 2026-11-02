"""处理器共用的权限、目标和延迟删除。"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from telegram import Chat, ChatPermissions, Message, Update, User
from telegram.constants import ChatType, MessageEntityType, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from groupbot.config import AppConfig, EffectiveSettings, resolve_settings
from groupbot.db import Database
from groupbot.permissions import AdminCache
from groupbot.textutil import html, humanize_telegram_error
from groupbot.timeutil import isoformat, utc_now

logger = logging.getLogger(__name__)

MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)

_USERNAME = re.compile(r"[A-Za-z0-9_]{5,32}")


@dataclass(frozen=True)
class Target:
    user_id: int
    name: str
    username: str | None = None


def database(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def config(context: ContextTypes.DEFAULT_TYPE) -> AppConfig:
    return context.application.bot_data["cfg"]


def admin_cache(context: ContextTypes.DEFAULT_TYPE) -> AdminCache:
    return context.application.bot_data["admin_cache"]


def is_group(chat: Chat | None) -> bool:
    return chat is not None and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}


def settings_dict(row: dict | None) -> dict:
    if not row:
        return {}
    try:
        data = json.loads(row.get("settings") or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def remember_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not is_group(chat) or chat is None:
        return
    await database(context).ensure_group(chat.id, chat.title, isoformat(utc_now()))


async def current_settings(
    context: ContextTypes.DEFAULT_TYPE,
    chat: Chat | None,
) -> EffectiveSettings:
    cfg = config(context)
    if not is_group(chat) or chat is None:
        return resolve_settings(cfg)
    row = await database(context).get_group(chat.id)
    welcome = row.get("welcome_msg") if row else None
    return resolve_settings(cfg, settings_dict(row), welcome)


async def caller_is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    if chat is None:
        return False
    sender = message.sender_chat if message is not None else None
    if sender is not None and sender.id == chat.id:
        return True
    if user is None:
        return False
    role = await admin_cache(context).role(context.bot, chat.id, user.id, config(context).admin_ids)
    return role == "admin"


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    chat = update.effective_chat
    if message is None:
        return False
    if not is_group(chat):
        await message.reply_text("请在群组中使用此命令。")
        return False
    if not await caller_is_admin(update, context):
        await message.reply_text("你没有权限使用此命令。")
        return False
    return True


def _display_name(user: User, fallback: str) -> str:
    return user.full_name or user.first_name or fallback


def _mention_target(message: Message) -> tuple[User, list[str]] | None:
    text = message.text or ""
    for entity in message.entities or []:
        if entity.type == MessageEntityType.TEXT_MENTION and entity.user is not None:
            rest = text[entity.offset + entity.length :].strip()
            return entity.user, rest.split() if rest else []
    return None


async def resolve_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[Target | None, list[str]]:
    message = update.effective_message
    args = [str(arg) for arg in (context.args or [])]
    if message is None:
        return None, args
    if message.reply_to_message is not None:
        replied = message.reply_to_message
        if replied.from_user is not None:
            user = replied.from_user
            return Target(user.id, _display_name(user, "用户"), user.username), args
        await message.reply_text("这条消息没有可操作的用户，请改用数字 ID。")
        return None, args

    mentioned = _mention_target(message)
    if mentioned is not None:
        user, rest = mentioned
        return Target(user.id, _display_name(user, "用户"), user.username), rest

    if not args:
        await message.reply_text("请回复对方的消息，或指定 @用户名 / 数字 ID。")
        return None, args

    token, rest = args[0], args[1:]
    if token.startswith("@"):
        username = token[1:]
        if _USERNAME.fullmatch(username) is None:
            await message.reply_text("用户名格式不正确。")
            return None, rest
        found = await database(context).find_by_username(username)
        if found:
            return (
                Target(found["user_id"], found["first_name"] or username, found["username"]),
                rest,
            )
        try:
            chat = await context.bot.get_chat(f"@{username}")
        except TelegramError:
            await message.reply_text(
                "找不到这个用户名。请回复对方消息，或使用数字 ID。"
                "对方需要先在群里说过话，机器人才能记下用户名。"
            )
            return None, rest
        name = getattr(chat, "full_name", None) or getattr(chat, "first_name", None) or username
        return Target(chat.id, name, getattr(chat, "username", None) or username), rest

    if token.isdigit():
        user_id = int(token)
        found = await database(context).find_user(user_id)
        name = (found or {}).get("first_name") or f"用户{user_id}"
        return Target(user_id, name, (found or {}).get("username")), rest

    await message.reply_text("请回复对方的消息，或指定 @用户名 / 数字 ID。")
    return None, args


async def block_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target: Target,
    *,
    allow_absent: bool,
) -> str | None:
    chat = update.effective_chat
    actor = update.effective_user
    if chat is None:
        return "请在群组中使用此命令。"
    if target.user_id == context.bot.id:
        return "不能对机器人本身执行此操作。"
    if actor is not None and target.user_id == actor.id:
        return "不能对自己执行此操作。"
    role = await admin_cache(context).role(
        context.bot,
        chat.id,
        target.user_id,
        config(context).admin_ids,
    )
    if role == "admin":
        return "不能对管理员执行此操作。"
    if role == "unknown":
        return "暂时无法确认对方身份，已取消操作。"
    if role == "absent" and not allow_absent:
        return "对方不在群里。"
    return None


async def mute_member(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    until: datetime | None,
) -> None:
    await context.bot.restrict_chat_member(
        chat_id,
        user_id,
        permissions=MUTED_PERMISSIONS,
        until_date=until,
        use_independent_chat_permissions=True,
    )


async def unmute_member(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    chat = await context.bot.get_chat(chat_id)
    permissions = chat.permissions or ChatPermissions.all_permissions()
    await context.bot.restrict_chat_member(
        chat_id,
        user_id,
        permissions=permissions,
        use_independent_chat_permissions=True,
    )


async def delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data if context.job is not None else None
    if not isinstance(data, dict):
        return
    try:
        await context.bot.delete_message(data["chat_id"], data["message_id"])
    except TelegramError:
        logger.debug("延迟删除消息失败", exc_info=True)


async def delete_later(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    delay: float,
) -> None:
    if delay <= 0:
        try:
            await context.bot.delete_message(chat_id, message_id)
        except TelegramError:
            logger.debug("删除消息失败", exc_info=True)
        return
    if context.job_queue is None:
        logger.error("任务队列不可用，无法延迟删除消息")
        return
    context.job_queue.run_once(
        delete_message_job,
        when=delay,
        data={"chat_id": chat_id, "message_id": message_id},
        name=f"del:{chat_id}:{message_id}",
    )


async def audit(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    user_id: int | None,
    actor_id: int | None,
    kind: str,
    action: str,
    detail: str | None = None,
    message_text: str | None = None,
) -> None:
    cfg = config(context)
    await database(context).log_action(
        chat_id=chat_id,
        user_id=user_id,
        actor_id=actor_id,
        kind=kind,
        action=action,
        detail=detail,
        message_text=message_text,
        created_at=isoformat(utc_now()),
        max_length=cfg.message_max_length,
    )
    row = await database(context).get_group(chat_id)
    welcome = row.get("welcome_msg") if row else None
    group_settings = resolve_settings(cfg, settings_dict(row), welcome)
    channel = group_settings.log_channel_id
    if not channel:
        return
    lines = [
        f"🛡 <b>{html(kind)}</b> · {html(action)}",
        f"群：<code>{chat_id}</code>",
        f"对象：<code>{user_id if user_id is not None else '—'}</code>",
        f"操作者：<code>{actor_id if actor_id is not None else '机器人'}</code>",
    ]
    if detail:
        lines.append(html(detail))
    if message_text:
        lines.append(f"内容：{html(message_text.strip()[:120])}")
    try:
        await context.bot.send_message(
            channel,
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError:
        logger.exception("转发管理日志失败")


def explain(exc: Exception) -> str:
    return humanize_telegram_error(exc)
