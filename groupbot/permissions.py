"""群管理员识别。结果缓存一小段时间，避免每条消息都打一次 API。"""

import logging
import time

from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, TelegramError

logger = logging.getLogger(__name__)

_ADMIN_STATUSES = {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}


class AdminCache:
    def __init__(self, ttl: float = 45.0) -> None:
        self.ttl = ttl
        self._roles: dict[tuple[int, int], tuple[float, str]] = {}

    async def role(
        self,
        bot: object,
        chat_id: int,
        user_id: int,
        super_admins: frozenset[int],
    ) -> str:
        """返回 admin、member、absent 或 unknown。超级管理员直接视为 admin。"""
        if user_id in super_admins:
            return "admin"
        cached = self._roles.get((chat_id, user_id))
        now = time.monotonic()
        if cached is not None and now - cached[0] < self.ttl:
            return cached[1]
        role = await fetch_role(bot, chat_id, user_id)
        if role in {"admin", "member"}:
            self._roles[(chat_id, user_id)] = (now, role)
        return role


async def fetch_role(bot: object, chat_id: int, user_id: int) -> str:
    try:
        member = await bot.get_chat_member(chat_id, user_id)  # type: ignore[attr-defined]
    except BadRequest as exc:
        text = str(exc).lower()
        if "user not found" in text or "participant" in text:
            return "absent"
        logger.warning("查询成员 %s 失败：%s", user_id, exc)
        return "unknown"
    except TelegramError as exc:
        logger.warning("查询成员 %s 失败：%s", user_id, exc)
        return "unknown"
    if member.status in _ADMIN_STATUSES:
        return "admin"
    return "member"
