"""组装应用并启动长轮询。"""

import logging
import sys

from telegram import BotCommand, BotCommandScopeAllChatAdministrators, Update
from telegram.error import InvalidToken, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    Defaults,
    MessageHandler,
    filters,
)

from groupbot.announce import sync_announcement_jobs
from groupbot.config import AppConfig, ConfigError, load_config
from groupbot.db import Database
from groupbot.handlers.admin import (
    addword_command,
    ban_command,
    clean_command,
    delword_command,
    kick_command,
    settings_command,
    setwelcome_command,
    stats_command,
    unban_command,
    unwarn_command,
    violations_command,
    warn_command,
    warns_command,
    wordlist_command,
)
from groupbot.handlers.errors import on_error
from groupbot.handlers.features import (
    delreply_command,
    panel_command,
    replies_command,
    reply_command,
    rules_command,
    setrules_command,
)
from groupbot.handlers.messages import (
    CONTENT_FILTER,
    member_left,
    moderate_message,
    resume_pending_verifications,
    rules_callback,
    verify_callback,
    welcome_new_members,
)
from groupbot.handlers.user import (
    dice_command,
    help_command,
    luck_command,
    points_command,
    rank_command,
    sign_command,
    start_command,
)
from groupbot.helptext import ADMIN_COMMANDS, USER_COMMANDS
from groupbot.jobs import schedule_maintenance
from groupbot.moderation import FloodTracker
from groupbot.panel_server import PanelState, start_panel
from groupbot.permissions import AdminCache
from groupbot.webapp_auth import SlidingLimiter

logger = logging.getLogger(__name__)

_USER_HANDLERS = {
    "start": start_command,
    "help": help_command,
    "sign": sign_command,
    "points": points_command,
    "rank": rank_command,
    "luck": luck_command,
    "dice": dice_command,
    "rules": rules_command,
    "panel": panel_command,
}

_ADMIN_HANDLERS = {
    "ban": ban_command,
    "unban": unban_command,
    "kick": kick_command,
    "warn": warn_command,
    "unwarn": unwarn_command,
    "warns": warns_command,
    "setwelcome": setwelcome_command,
    "addword": addword_command,
    "delword": delword_command,
    "wordlist": wordlist_command,
    "settings": settings_command,
    "stats": stats_command,
    "clean": clean_command,
    "violations": violations_command,
    "setrules": setrules_command,
    "reply": reply_command,
    "delreply": delreply_command,
    "replies": replies_command,
}


def register_handlers(app: Application) -> None:
    for name, callback in _USER_HANDLERS.items():
        app.add_handler(CommandHandler(name, callback))
    for name, callback in _ADMIN_HANDLERS.items():
        app.add_handler(CommandHandler(name, callback))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern=r"^v:"))
    app.add_handler(CallbackQueryHandler(rules_callback, pattern=r"^rules$"))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, member_left))
    app.add_handler(MessageHandler(CONTENT_FILTER, moderate_message))
    app.add_error_handler(on_error)


def build_application(cfg: AppConfig) -> Application:
    app = (
        Application.builder()
        .token(cfg.token)
        .defaults(Defaults(tzinfo=cfg.timezone()))
        .concurrent_updates(True)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["cfg"] = cfg
    register_handlers(app)
    return app


async def post_init(app: Application) -> None:
    cfg: AppConfig = app.bot_data["cfg"]
    store = Database(cfg.db_path)
    await store.connect()
    app.bot_data["db"] = store
    app.bot_data["admin_cache"] = AdminCache()
    app.bot_data["flood"] = FloodTracker()
    schedule_maintenance(app)
    await resume_pending_verifications(app)
    await sync_announcement_jobs(app)
    await publish_commands(app)
    runner = await start_panel(
        PanelState(
            cfg=cfg,
            db=store,
            bot=app.bot,
            admin_cache=app.bot_data["admin_cache"],
            limiter=SlidingLimiter(),
            sync_announcements=lambda chat_id: sync_announcement_jobs(app, chat_id),
        )
    )
    if runner is not None:
        app.bot_data["panel_runner"] = runner
    logger.info("数据库已就绪：%s", cfg.db_path)


async def post_shutdown(app: Application) -> None:
    runner = app.bot_data.get("panel_runner")
    if runner is not None:
        await runner.cleanup()
    store = app.bot_data.get("db")
    if isinstance(store, Database):
        await store.close()


async def publish_commands(app: Application) -> None:
    user_commands = [BotCommand(name, description) for name, description in USER_COMMANDS]
    admin_commands = user_commands + [
        BotCommand(name, description) for name, description in ADMIN_COMMANDS
    ]
    try:
        await app.bot.set_my_commands(user_commands)
        await app.bot.set_my_commands(
            admin_commands,
            scope=BotCommandScopeAllChatAdministrators(),
        )
    except TelegramError:
        logger.exception("设置命令菜单失败，手动输入命令仍然可用")


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    app = build_application(cfg)
    if app.job_queue is None:
        print("❌ 未启用任务队列，请安装 python-telegram-bot[job-queue]。", file=sys.stderr)
        raise SystemExit(1)
    logger.info("超级群管机器人启动中")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except InvalidToken:
        logger.error("Telegram 拒绝了 Bot Token，请检查 BOT_TOKEN 是否正确。")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
