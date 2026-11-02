"""群设置的增删和校验。命令和网页面板共用这里，避免两套数据。"""

import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

_KEYWORD_LIMIT = 200
_PATTERN_LIMIT = 50
_REPLY_LIMIT = 30
_ANNOUNCE_LIMIT = 10
_BUTTON_LIMIT = 6


@dataclass(frozen=True)
class AutoReply:
    keyword: str
    response: str
    mode: str


@dataclass(frozen=True)
class Announcement:
    id: str
    text: str
    interval_minutes: int
    enabled: bool


@dataclass(frozen=True)
class WelcomeButton:
    text: str
    action: str
    url: str


def _strings(overrides: dict, key: str) -> list[str]:
    value = overrides.get(key, [])
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def mutate_keywords(
    overrides: dict,
    base: tuple[str, ...],
    word: str,
    *,
    remove: bool,
) -> tuple[dict, str | None]:
    cleaned = word.strip()
    if not cleaned or len(cleaned) > 32:
        return {}, "关键词需要 1 到 32 个字符。"
    extra = _strings(overrides, "extra_keywords")
    disabled = _strings(overrides, "disabled_keywords")
    folded = cleaned.casefold()
    effective = {
        item.casefold()
        for item in (*base, *extra)
        if item.casefold() not in {entry.casefold() for entry in disabled}
    }
    if remove:
        if folded not in effective:
            return {}, "没有这个屏蔽词。"
        extra = [item for item in extra if item.casefold() != folded]
        if any(item.casefold() == folded for item in base):
            if folded not in {item.casefold() for item in disabled}:
                disabled.append(cleaned)
    else:
        if folded in effective:
            return {}, "这个词已经在列表里。"
        disabled = [item for item in disabled if item.casefold() != folded]
        known = {item.casefold() for item in (*base, *extra)}
        if folded not in known:
            if len(extra) >= _KEYWORD_LIMIT:
                return {}, "本群额外屏蔽词已达上限。"
            extra.append(cleaned)
    return {"extra_keywords": extra, "disabled_keywords": disabled}, None


def validate_pattern(pattern: str) -> str | None:
    if not pattern or len(pattern) > 200:
        return "正则需要 1 到 200 个字符。"
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error:
        return "这条正则无法编译。"
    return None


def mutate_patterns(
    overrides: dict,
    base: tuple[str, ...],
    pattern: str,
    *,
    remove: bool,
) -> tuple[dict, str | None]:
    cleaned = pattern.strip()
    if not remove:
        error = validate_pattern(cleaned)
        if error:
            return {}, error
    extra = _strings(overrides, "extra_patterns")
    disabled = _strings(overrides, "disabled_patterns")
    effective = [item for item in (*base, *extra) if item not in set(disabled)]
    if remove:
        if cleaned not in effective:
            return {}, "没有这条正则。"
        extra = [item for item in extra if item != cleaned]
        if cleaned in base and cleaned not in disabled:
            disabled.append(cleaned)
    else:
        if cleaned in effective:
            return {}, "这条正则已经存在。"
        disabled = [item for item in disabled if item != cleaned]
        if cleaned not in base and cleaned not in extra:
            if len(extra) >= _PATTERN_LIMIT:
                return {}, "本群额外正则已达上限。"
            extra.append(cleaned)
    return {"extra_patterns": extra, "disabled_patterns": disabled}, None


def mutate_domains(
    overrides: dict,
    base: tuple[str, ...],
    domain: str,
    *,
    remove: bool,
) -> tuple[dict, str | None]:
    from groupbot.textutil import normalize_domain

    cleaned = normalize_domain(domain)
    if cleaned is None:
        return {}, "域名格式不正确。"
    extra = _strings(overrides, "extra_whitelist")
    disabled = _strings(overrides, "disabled_whitelist")

    def folded(items: list[str] | tuple[str, ...]) -> set[str]:
        return {(normalize_domain(item) or item.lower()) for item in items}

    effective = folded(base) | folded(extra)
    effective -= folded(disabled)
    if remove:
        if cleaned not in effective and cleaned not in folded(extra):
            return {}, "白名单里没有这个域名。"
        extra = [item for item in extra if (normalize_domain(item) or item.lower()) != cleaned]
        if cleaned in folded(base) and cleaned not in folded(disabled):
            disabled.append(cleaned)
        action = "del"
    else:
        disabled = [
            item for item in disabled if (normalize_domain(item) or item.lower()) != cleaned
        ]
        if cleaned not in folded(base) and cleaned not in folded(extra):
            extra.append(cleaned)
        action = "add"
    return {"extra_whitelist": extra, "disabled_whitelist": disabled}, None if action else None


def parse_replies(raw: object) -> tuple[list[dict], str | None]:
    if not isinstance(raw, list):
        return [], "自动回复格式不正确。"
    if len(raw) > _REPLY_LIMIT:
        return [], f"最多 {_REPLY_LIMIT} 条自动回复。"
    items: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            return [], "自动回复格式不正确。"
        keyword = entry.get("keyword")
        response = entry.get("response")
        mode = entry.get("mode", "contains")
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword.strip()) > 32:
            return [], "回复关键词需要 1 到 32 个字符。"
        if not isinstance(response, str) or not response.strip() or len(response) > 1000:
            return [], "回复内容需要 1 到 1000 个字符。"
        if mode not in {"contains", "exact"}:
            return [], "匹配方式只能是包含或完全一致。"
        folded = keyword.strip().casefold()
        if folded in seen:
            return [], "自动回复里有重复的关键词。"
        seen.add(folded)
        items.append({"keyword": keyword.strip(), "response": response.strip(), "mode": mode})
    return items, None


def parse_announcements(raw: object) -> tuple[list[dict], str | None]:
    if not isinstance(raw, list):
        return [], "公告格式不正确。"
    if len(raw) > _ANNOUNCE_LIMIT:
        return [], f"最多 {_ANNOUNCE_LIMIT} 条定时公告。"
    items: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            return [], "公告格式不正确。"
        text = entry.get("text")
        interval = entry.get("interval_minutes")
        enabled = entry.get("enabled", True)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 3500:
            return [], "公告内容需要 1 到 3500 个字符。"
        if isinstance(interval, bool) or not isinstance(interval, int):
            return [], "公告间隔必须是整数分钟。"
        if not 15 <= interval <= 43200:
            return [], "公告间隔需要在 15 分钟到 30 天之间。"
        if not isinstance(enabled, bool):
            return [], "公告开关不正确。"
        ann_id = entry.get("id")
        if not isinstance(ann_id, str) or not re.fullmatch(r"[A-Za-z0-9]{4,16}", ann_id):
            ann_id = secrets.token_hex(4)
        while ann_id in seen:
            ann_id = secrets.token_hex(4)
        seen.add(ann_id)
        items.append(
            {
                "id": ann_id,
                "text": text.strip(),
                "interval_minutes": interval,
                "enabled": enabled,
            }
        )
    return items, None


def _https_url(url: str) -> bool:
    if not url or len(url) > 200 or any(char.isspace() for char in url):
        return False
    parsed = urlsplit(url)
    return parsed.scheme == "https" and bool(parsed.hostname)


def parse_buttons(raw: object) -> tuple[list[dict], str | None]:
    if not isinstance(raw, list):
        return [], "按钮格式不正确。"
    if len(raw) > _BUTTON_LIMIT:
        return [], f"最多 {_BUTTON_LIMIT} 个欢迎按钮。"
    items: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return [], "按钮格式不正确。"
        text = entry.get("text")
        action = entry.get("action", "url")
        url = entry.get("url", "")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 32:
            return [], "按钮文字需要 1 到 32 个字符。"
        if action not in {"url", "rules"}:
            return [], "按钮类型不正确。"
        if not isinstance(url, str):
            return [], "按钮链接不正确。"
        if action == "url" and not _https_url(url.strip()):
            return [], "按钮链接必须是 https 地址。"
        items.append(
            {
                "text": text.strip(),
                "action": action,
                "url": url.strip() if action == "url" else "",
            }
        )
    return items, None


def replies_from(overrides: dict) -> tuple[AutoReply, ...]:
    items, error = parse_replies(overrides.get("auto_replies", []))
    if error and overrides.get("auto_replies") not in (None, []):
        return ()
    return tuple(AutoReply(item["keyword"], item["response"], item["mode"]) for item in items)


def announcements_from(overrides: dict) -> tuple[Announcement, ...]:
    raw = overrides.get("announcements", [])
    if not isinstance(raw, list):
        return ()
    chosen: list[Announcement] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        interval = entry.get("interval_minutes")
        enabled = entry.get("enabled", True)
        ann_id = entry.get("id")
        if not isinstance(text, str) or not isinstance(interval, int) or isinstance(interval, bool):
            continue
        if not isinstance(ann_id, str) or not isinstance(enabled, bool):
            continue
        chosen.append(Announcement(ann_id, text, interval, enabled))
    return tuple(chosen)


def buttons_from(overrides: dict) -> tuple[WelcomeButton, ...]:
    raw = overrides.get("welcome_buttons", [])
    if not isinstance(raw, list):
        return ()
    chosen: list[WelcomeButton] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        action = entry.get("action", "url")
        url = entry.get("url", "")
        if not isinstance(text, str) or action not in {"url", "rules"} or not isinstance(url, str):
            continue
        if action == "url" and not _https_url(url):
            continue
        chosen.append(WelcomeButton(text, action, url if action == "url" else ""))
    return tuple(chosen)


def match_reply(text: str, replies: tuple[AutoReply, ...]) -> str | None:
    folded = text.casefold().strip()
    if not folded:
        return None
    for reply in replies:
        keyword = reply.keyword.casefold()
        if reply.mode == "exact" and folded == keyword:
            return reply.response
        if reply.mode == "contains" and keyword in folded:
            return reply.response
    return None


BOOL_FIELDS = (
    "anti_ad_enabled",
    "ban_links",
    "ban_channel_messages",
    "ban_forwards",
    "verify_enabled",
    "delete_service",
    "delete_leave",
    "flood_enabled",
    "welcome_enabled",
    "auto_reply_enabled",
    "announce_enabled",
    "rules_enabled",
    "points_enabled",
)

NUMBER_FIELDS = {
    "new_user_restrict_minutes": (0, 10080),
    "mute_duration": (0, 366 * 24 * 3600),
    "warn_threshold": (1, 20),
    "verify_timeout": (15, 3600),
    "flood_max": (2, 50),
    "flood_window": (2, 300),
    "flood_mute": (30, 86400),
    "sign_daily": (0, 10000),
    "msg_per_point": (0, 10000),
    "max_points_per_day": (0, 100000),
    "dice_cost": (0, 100000),
    "dice_reward": (0, 100000),
    "dice_reward_threshold": (1, 6),
}

VIOLATION_KINDS = (
    "keyword",
    "regex",
    "link",
    "new_user_link",
    "channel",
    "forward",
    "flood",
)
AUDIT_KINDS = (
    "ban",
    "unban",
    "kick",
    "warn",
    "unwarn",
    "settings",
    "clean",
    "verify_kick",
    "panel",
)


def group_access_decision(role: str) -> str | None:
    """管理员返回 None。查不到身份时拒绝，避免误放行。"""
    if role == "admin":
        return None
    if role == "unknown":
        return "unknown"
    return "forbidden"
