"""只记日志。不要把内部错误再发回群里。"""

import logging

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from groupbot.textutil import html

logger = logging.getLogger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("处理更新失败", exc_info=context.error)
    cfg = context.application.bot_data.get("cfg")
    channel = getattr(cfg, "log_channel_id", None)
    if not channel:
        return
    name = type(context.error).__name__ if context.error else "Error"
    try:
        await context.bot.send_message(
            channel,
            f"⚠️ 机器人内部错误：{html(name)}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        logger.exception("无法向日志频道报告错误")
