"""文案、时长解析和 HTML 转义。"""

import re
from datetime import timedelta
from html import escape

_DURATION = re.compile(r"^(\d+)([smhd])?$", re.IGNORECASE)
_TRUE = {"on", "true", "1", "开", "yes", "y"}
_FALSE = {"off", "false", "0", "关", "no", "n"}
_DOMAIN = re.compile(r"^[a-z0-9.-]+$")


def html(value: object) -> str:
    return escape(str(value), quote=False)


def mention(user_id: int, name: str) -> str:
    safe = html(name.strip() or "用户")
    return f'<a href="tg://user?id={int(user_id)}">{safe}</a>'


def render_template(template: str, **values: object) -> str:
    """先转义模板，再一次性填入占位值，避免用户名里的花括号被再次替换。"""
    pieces = re.split(r"\{([a-z_]+)\}", html(template))
    rendered: list[str] = []
    for index, piece in enumerate(pieces):
        if index % 2 == 0:
            rendered.append(piece)
        else:
            rendered.append(html(values.get(piece, "{" + piece + "}")))
    return "".join(rendered)


def parse_duration(token: str) -> timedelta | None:
    """裸数字按分钟，和原来的 /ban 60 保持一致。后缀 s/m/h/d 另行指定单位。"""
    match = _DURATION.fullmatch(token.strip())
    if match is None:
        return None
    amount = int(match.group(1))
    unit = (match.group(2) or "m").lower()
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return timedelta(seconds=amount * factor)


def parse_bool(token: str) -> bool | None:
    value = token.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "永久"
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分钟"
    if seconds < 86400:
        hours, rest = divmod(seconds, 3600)
        minutes = rest // 60
        if minutes:
            return f"{hours}小时{minutes}分钟"
        return f"{hours}小时"
    days, rest = divmod(seconds, 86400)
    hours = rest // 3600
    if hours:
        return f"{days}天{hours}小时"
    return f"{days}天"


def normalize_domain(raw: str) -> str | None:
    text = raw.strip().lower()
    if not text:
        return None
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)
    text = text.split("/")[0].split("?")[0].split("#")[0]
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    if text.startswith("www."):
        text = text[4:]
    if ":" in text:
        text = text.split(":", 1)[0]
    text = text.rstrip(".")
    if not text or "." not in text or text.startswith(".") or ".." in text:
        return None
    if not _DOMAIN.fullmatch(text):
        return None
    return text


def humanize_telegram_error(exc: Exception) -> str:
    text = str(exc).lower()
    if any(
        piece in text
        for piece in ("not enough rights", "need administrator", "chat_admin_required")
    ):
        return "操作失败：请确认机器人是管理员，且拥有封禁和删除消息的权限。"
    if any(piece in text for piece in ("user_not_participant", "user not found", "participant")):
        return "操作失败：找不到该用户，或对方不在群里。"
    if any(piece in text for piece in ("administrator", "can't remove chat owner", "creator")):
        return "不能对管理员执行此操作。"
    if "message to delete not found" in text or "message can't be deleted" in text:
        return "消息已不存在，或机器人无权删除。"
    return "操作失败，请稍后重试。"
