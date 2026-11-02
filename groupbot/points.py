"""签到、发言积分和每日运势。纯函数，方便测试。"""

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta

FORTUNES: tuple[tuple[str, str, str], ...] = (
    ("大吉", "🌟", "今天运气爆棚，做什么都顺利！"),
    ("中吉", "✨", "运势不错，把握机会！"),
    ("小吉", "🍀", "小有收获，保持平常心。"),
    ("平", "😐", "平平淡淡才是真。"),
    ("小凶", "⚠️", "小心行事，避免冲动。"),
    ("凶", "💀", "今天不太顺利，低调为好。"),
)
COLORS: tuple[str, ...] = ("红色", "蓝色", "绿色", "黄色", "紫色", "粉色", "橙色")


@dataclass(frozen=True)
class SignOutcome:
    already: bool
    streak: int
    awarded: int
    bonus: int


@dataclass(frozen=True)
class Fortune:
    name: str
    emoji: str
    summary: str
    lucky_number: int
    lucky_color: str


@dataclass(frozen=True)
class MessagePointState:
    total_msgs: int
    points_today: int
    points_date: date | None


def sign_outcome(
    last_sign: date | None,
    streak: int,
    today: date,
    daily: int,
    bonus_days: tuple[int, ...] | list[int],
) -> SignOutcome:
    """连续签到奖励与旧版一致：命中奖励日时额外再给 daily * 2。"""
    if last_sign == today:
        return SignOutcome(already=True, streak=streak, awarded=0, bonus=0)
    if last_sign is not None and last_sign == today - timedelta(days=1):
        new_streak = streak + 1
    else:
        new_streak = 1
    bonus = daily * 2 if new_streak in set(bonus_days) else 0
    return SignOutcome(already=False, streak=new_streak, awarded=daily + bonus, bonus=bonus)


def apply_message_points(
    state: MessagePointState,
    today: date,
    msg_per_point: int,
    max_per_day: int,
) -> tuple[MessagePointState, int]:
    """每累计 msg_per_point 条消息得 1 分，当天发言积分不超过 max_per_day。"""
    points_today = state.points_today if state.points_date == today else 0
    total = state.total_msgs + 1
    gained = 0
    if (
        msg_per_point > 0
        and max_per_day > 0
        and total % msg_per_point == 0
        and points_today < max_per_day
    ):
        gained = 1
        points_today += 1
    return MessagePointState(total, points_today, today), gained


def daily_fortune(user_id: int, day: date) -> Fortune:
    """用 SHA-256 固定当天结果，不使用会随进程变化的内置 hash()。"""
    digest = hashlib.sha256(f"{user_id}:{day.isoformat()}".encode()).digest()
    number = int.from_bytes(digest[:8], "big")
    name, emoji, summary = FORTUNES[number % len(FORTUNES)]
    return Fortune(
        name=name,
        emoji=emoji,
        summary=summary,
        lucky_number=number % 100,
        lucky_color=COLORS[(number // 7) % len(COLORS)],
    )
