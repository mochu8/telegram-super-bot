"""群规、自动回复和管理面板入口。"""

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from groupbot.groupsettings import parse_replies
from groupbot.services import (
    caller_is_admin,
    config,
    current_settings,
    database,
    is_group,
    remember_group,
    require_admin,
)
from groupbot.textutil import html

logger = logging.getLogger(__name__)

_GROUP_ARG = re.compile(r"g(-?\d+)$")


def parse_group_arg(token: str) -> int | None:
    match = _GROUP_ARG.fullmatch(token.strip())
    if match is None:
        return None
    value = int(match.group(1))
    return value or None


def panel_url(base: str, chat_id: int | None = None) -> str:
    if chat_id is None:
        return f"{base}/"
    return f"{base}/?chat={chat_id}"


def panel_keyboard(base: str, chat_id: int | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("进入管理面板", web_app=WebAppInfo(url=panel_url(base, chat_id)))]]
    )


async def _username(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    cached = context.application.bot_data.get("bot_username")
    if isinstance(cached, str) and cached:
        return cached
    me = await context.bot.get_me()
    if me.username:
        context.application.bot_data["bot_username"] = me.username
    return me.username


async def _menu_button(context: ContextTypes.DEFAULT_TYPE, chat_id: int, base: str) -> None:
    try:
        await context.bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(text="面板", web_app=WebAppInfo(url=panel_url(base))),
        )
    except TelegramError:
        logger.info("设置菜单按钮失败 chat=%s", chat_id)


async def panel_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    base = config(context).panel_base_url
    if not base:
        await message.reply_text("管理面板没有启用。")
        return
    if is_group(chat):
        if not await caller_is_admin(update, context):
            await message.reply_text("只有管理员可以打开面板。")
            return
        try:
            username = await _username(context)
        except TelegramError:
            await message.reply_text("暂时无法生成面板按钮。")
            return
        if not username:
            await message.reply_text("机器人还没有用户名，无法生成链接。")
            return
        direct = f"https://t.me/{username}?startapp=g{chat.id}"
        private = f"https://t.me/{username}?start=g{chat.id}"
        await message.reply_text(
            "群里的按钮会打开 Mini App。如果它只是进入了私聊，再点机器人发来的「进入管理面板」。",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("打开管理面板", url=direct)],
                    [InlineKeyboardButton("改到私聊打开", url=private)],
                ]
            ),
        )
        return
    await _menu_button(context, chat.id, base)
    await message.reply_text(
        "在下面打开面板。里面只列出你是管理员的群。",
        reply_markup=panel_keyboard(base),
    )


async def rules_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not is_group(chat):
        await message.reply_text("请在群里查看群规。")
        return
    settings = await current_settings(context, chat)
    if not settings.rules_enabled or not settings.rules_text.strip():
        await message.reply_text("本群还没有群规。")
        return
    await message.reply_text(settings.rules_text)


async def setrules_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    await remember_group(update, context)
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    body = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        body = parts[1].strip() if len(parts) > 1 else ""
    if not body:
        settings = await current_settings(context, chat)
        await message.reply_text(settings.rules_text or "还没有群规。发送 /setrules 文本 来设置。")
        return
    if body in {"-", "清空", "默认"}:
        body = ""
    if len(body) > 3500:
        await message.reply_text("群规不能超过 3500 个字符。")
        return
    await database(context).patch_settings(chat.id, {"rules_text": body, "rules_enabled": True})
    await message.reply_text("已更新群规。" if body else "已清空群规。")


async def reply_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    await remember_group(update, context)
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return
    parts = message.text.split(maxsplit=1)
    body = parts[1].strip() if len(parts) > 1 else ""
    if "|" not in body:
        await message.reply_text("用法：/reply 关键词 | 回复内容")
        return
    keyword, response = (part.strip() for part in body.split("|", 1))
    settings = await current_settings(context, chat)
    items = [
        {"keyword": item.keyword, "response": item.response, "mode": item.mode}
        for item in settings.auto_replies
        if item.keyword.casefold() != keyword.casefold()
    ]
    items.append({"keyword": keyword, "response": response, "mode": "contains"})
    parsed, error = parse_replies(items)
    if error:
        await message.reply_text(error)
        return
    await database(context).patch_settings(
        chat.id,
        {"auto_replies": parsed, "auto_reply_enabled": True},
    )
    await message.reply_text(f"已设置自动回复：{keyword}")


async def delreply_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    await remember_group(update, context)
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    word = " ".join(context.args or []).strip()
    if not word:
        await message.reply_text("用法：/delreply 关键词")
        return
    settings = await current_settings(context, chat)
    kept = [
        {"keyword": item.keyword, "response": item.response, "mode": item.mode}
        for item in settings.auto_replies
        if item.keyword.casefold() != word.casefold()
    ]
    if len(kept) == len(settings.auto_replies):
        await message.reply_text("没有这个自动回复。")
        return
    await database(context).patch_settings(chat.id, {"auto_replies": kept})
    await message.reply_text("已删除自动回复。")


async def replies_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    settings = await current_settings(context, chat)
    if not settings.auto_replies:
        await message.reply_text("还没有自动回复。")
        return
    lines = ["<b>自动回复</b>", ""]
    for item in settings.auto_replies:
        lines.append(f"• {html(item.keyword)} → {html(item.response)}")
    await message.reply_text("\n".join(lines), parse_mode="HTML")
