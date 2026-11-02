"""定时备份和消息记录清理。"""

from datetime import timedelta
from pathlib import Path

from telegram.ext import Application, ContextTypes

from groupbot.config import AppConfig, backup_interval_hours
from groupbot.timeutil import isoformat, utc_now


def maintenance_plan(cfg: AppConfig) -> list[tuple[str, timedelta]]:
    plan = [("retention", timedelta(hours=1))]
    hours = backup_interval_hours(cfg)
    if hours is not None:
        plan.append(("db-backup", timedelta(hours=hours)))
    return plan


async def backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: AppConfig = context.application.bot_data["cfg"]
    if backup_interval_hours(cfg) is None:
        return
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    destination = Path(cfg.backup_dir) / f"bot-{stamp}.db"
    await context.application.bot_data["db"].backup(destination)
    files = sorted(Path(cfg.backup_dir).glob("bot-*.db"))
    for old in files[: max(0, len(files) - cfg.backup_keep)]:
        old.unlink(missing_ok=True)


async def retention_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: AppConfig = context.application.bot_data["cfg"]
    cutoff = utc_now() - timedelta(days=cfg.message_retention_days)
    await context.application.bot_data["db"].purge_messages(
        isoformat(cutoff),
        cfg.message_max_rows,
    )


def schedule_maintenance(app: Application) -> None:
    queue = app.job_queue
    if queue is None:
        raise RuntimeError("需要安装 python-telegram-bot[job-queue]，定时删除和备份无法工作。")
    cfg: AppConfig = app.bot_data["cfg"]
    for name, interval in maintenance_plan(cfg):
        callback = retention_job if name == "retention" else backup_job
        first: float | timedelta = 60 if name == "retention" else interval
        queue.run_repeating(callback, interval=interval, first=first, name=name)
