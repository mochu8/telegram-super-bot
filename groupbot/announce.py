"""按群定时公告。任务在进程内，重启后会按当前设置重新登记。"""

import logging
from datetime import timedelta

from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from groupbot.config import resolve_settings
from groupbot.services import settings_dict

logger = logging.getLogger(__name__)


def announcement_job_name(chat_id: int, announcement_id: str) -> str:
    return f"ann:{chat_id}:{announcement_id}"


async def announcement_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data if context.job is not None else None
    if not isinstance(data, dict):
        return
    chat_id = int(data["chat_id"])
    announcement_id = str(data["id"])
    database = context.application.bot_data["db"]
    cfg = context.application.bot_data["cfg"]
    row = await database.get_group(chat_id)
    if row is None:
        return
    settings = resolve_settings(cfg, settings_dict(row), row.get("welcome_msg"))
    if not settings.announce_enabled:
        return
    text = ""
    for item in settings.announcements:
        if item.id == announcement_id and item.enabled:
            text = item.text
            break
    if not text:
        return
    try:
        await context.bot.send_message(chat_id, text, disable_web_page_preview=True)
    except TelegramError:
        logger.warning("发送定时公告失败 chat=%s", chat_id)


async def sync_announcement_jobs(app: Application, chat_id: int | None = None) -> None:
    queue = app.job_queue
    database = app.bot_data.get("db")
    cfg = app.bot_data.get("cfg")
    if queue is None or database is None or cfg is None:
        return
    if chat_id is None:
        groups = await database.list_groups()
    else:
        row = await database.get_group(chat_id)
        groups = [row] if row else []
    for group in groups:
        current_id = int(group["chat_id"])
        prefix = f"ann:{current_id}:"
        for job in queue.jobs():
            if job.name and job.name.startswith(prefix):
                job.schedule_removal()
        full = await database.get_group(current_id)
        if full is None:
            continue
        settings = resolve_settings(cfg, settings_dict(full), full.get("welcome_msg"))
        if not settings.announce_enabled:
            continue
        for item in settings.announcements:
            if not item.enabled or not 15 <= item.interval_minutes <= 43200:
                continue
            interval = timedelta(minutes=max(item.interval_minutes, 15))
            queue.run_repeating(
                announcement_job,
                interval=interval,
                first=interval,
                data={"chat_id": current_id, "id": item.id},
                name=announcement_job_name(current_id, item.id),
            )
