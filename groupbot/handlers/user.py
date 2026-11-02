"""签到、积分、运势和骰子。数据按 chat_id + user_id 分开。"""

import logging
from zoneinfo import ZoneInfo

from telegram.constants import DiceEmoji, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from groupbot.handlers.features import panel_keyboard, parse_group_arg
from groupbot.helptext import HELP_TEXT
from groupbot.points import daily_fortune
from groupbot.services import config, current_settings, database, is_group, remember_group
from groupbot.textutil import html
from groupbot.timeutil import isoformat, local_today, utc_now

logger = logging.getLogger(__name__)


async def _touch(update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message
    if user is None or chat is None or message is None:
        return None
    await database(context).ensure_member(
        chat.id,
        user.id,
        user.username,
        user.first_name or "用户",
        isoformat(utc_now()),
    )
    if is_group(chat):
        await remember_group(update, context)
    return message


async def start_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _touch(update, context)
    user = update.effective_user
    if message is None or user is None:
        return
    args = context.args or []
    if args:
        linked = parse_group_arg(str(args[0]))
        base = config(context).panel_base_url
        if linked is not None and base:
            await message.reply_text(
                "点下面的按钮管理这个群。只有管理员能修改设置。",
                reply_markup=panel_keyboard(base, linked),
            )
            return
    name = html(user.first_name or "朋友")
    await message.reply_text(
        f"👋 你好，{name}！\n\n"
        "我可以帮这个群过滤广告、管理成员，也带了签到和积分。\n"
        "发送 /help 查看命令。积分和签到按群分别计算。",
        parse_mode=ParseMode.HTML,
    )


async def help_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def sign_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _touch(update, context)
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    settings = await current_settings(context, chat)
    if not settings.points_enabled:
        await message.reply_text("本群已关闭积分功能。")
        return
    today = local_today(utc_now(), ZoneInfo(settings.timezone_name))
    outcome, total = await database(context).sign_in(
        chat.id,
        user.id,
        today,
        settings.sign_daily,
        settings.sign_bonus,
    )
    if outcome.already:
        await message.reply_text("你今天已经在这里签到过了，明天再来吧。")
        return
    if outcome.bonus:
        bonus = f"🎁 连续签到 {outcome.streak} 天，额外奖励 {outcome.bonus} 积分！"
    else:
        bonus = f"🔥 连续签到 {outcome.streak} 天"
    await message.reply_text(
        f"✅ 签到成功！\n\n获得 {outcome.awarded} 积分\n{bonus}\n总积分：{total}"
    )


async def points_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _touch(update, context)
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    settings = await current_settings(context, chat)
    if not settings.points_enabled:
        await message.reply_text("本群已关闭积分功能。")
        return
    member = await database(context).get_member(chat.id, user.id)
    if member is None:
        await message.reply_text("你在这里还没有积分，先用 /sign 签到吧。")
        return
    await message.reply_text(
        "💰 <b>我的积分</b>\n\n"
        f"用户：{html(user.full_name or user.first_name or '用户')}\n"
        f"积分：{member['points']}\n"
        f"消息：{member['total_msgs']} 条\n"
        f"连续签到：{member['sign_streak']} 天",
        parse_mode=ParseMode.HTML,
    )


async def rank_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if is_group(chat):
        await remember_group(update, context)
    settings = await current_settings(context, chat)
    if not settings.points_enabled:
        await message.reply_text("本群已关闭积分功能。")
        return
    rows = await database(context).ranking(chat.id, 10)
    if not rows:
        await message.reply_text("暂无排行数据。")
        return
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = ["🏆 <b>本群活跃度排行</b>", ""]
    for index, row in enumerate(rows):
        name = row["first_name"] or row["username"] or f"用户{row['user_id']}"
        badge = medals[index] if index < len(medals) else "•"
        lines.append(f"{badge} {html(name)} - {row['points']} 积分（{row['total_msgs']} 条消息）")
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def luck_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    settings = await current_settings(context, chat)
    fortune = daily_fortune(user.id, local_today(utc_now(), ZoneInfo(settings.timezone_name)))
    await message.reply_text(
        "🔮 <b>今日运势</b>\n\n"
        f"{fortune.emoji} <b>{html(fortune.name)}</b>\n"
        f"{html(fortune.summary)}\n\n"
        f"幸运数字：{fortune.lucky_number}\n"
        f"幸运颜色：{html(fortune.lucky_color)}",
        parse_mode=ParseMode.HTML,
    )


async def dice_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _touch(update, context)
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    settings = await current_settings(context, chat)
    if not settings.points_enabled:
        await message.reply_text("本群已关闭积分功能。")
        return
    store = database(context)
    if not await store.try_spend(chat.id, user.id, settings.dice_cost):
        member = await store.get_member(chat.id, user.id)
        owned = member["points"] if member else 0
        await message.reply_text(f"积分不足。需要 {settings.dice_cost}，你有 {owned}。")
        return
    try:
        sent = await message.reply_dice(emoji=DiceEmoji.DICE)
    except TelegramError:
        logger.exception("发送骰子失败")
        if settings.dice_cost:
            await store.add_points(chat.id, user.id, settings.dice_cost)
        await message.reply_text("骰子发送失败，积分已退回。")
        return
    value = sent.dice.value if sent.dice is not None else 1
    reward = 0
    if value >= settings.dice_reward_threshold:
        reward = settings.dice_reward
        await store.add_points(chat.id, user.id, reward)
    member = await store.get_member(chat.id, user.id)
    total = member["points"] if member else 0
    result = f"恭喜，获得 {reward} 积分！" if reward else "再接再厉。"
    await message.reply_text(
        f"🎲 点数：{value}\n消耗：{settings.dice_cost} 积分\n{result}\n当前积分：{total}"
    )
