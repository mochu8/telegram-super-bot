"""时区与禁言时长。所有对外时间都带时区。"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

MIN_TIMED_MUTE_SECONDS = 30
MAX_TIMED_MUTE_SECONDS = 366 * 24 * 60 * 60


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("时间必须带时区")
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def local_today(moment: datetime, tz: ZoneInfo) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(tz).date()


def format_local(value: str, tz: ZoneInfo) -> str:
    return parse_iso(value).astimezone(tz).strftime("%Y-%m-%d %H:%M")


def clamp_mute_seconds(seconds: int) -> tuple[int, str]:
    """返回实际禁言秒数（0 表示永久）和需要告诉管理员的说明。"""
    if seconds <= 0:
        return 0, ""
    if seconds < MIN_TIMED_MUTE_SECONDS:
        return MIN_TIMED_MUTE_SECONDS, "（Telegram 最短临时禁言为 30 秒）"
    if seconds >= MAX_TIMED_MUTE_SECONDS:
        return 0, "（超过 366 天，已按永久禁言处理）"
    return seconds, ""


def mute_until(now: datetime, seconds: int) -> datetime | None:
    """临时禁言的截止时间（UTC）。永久禁言返回 None。"""
    actual, _note = clamp_mute_seconds(seconds)
    if actual <= 0:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC) + timedelta(seconds=actual)
