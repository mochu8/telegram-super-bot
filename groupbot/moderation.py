"""广告、链接、频道身份和刷屏判断。不依赖 Telegram 对象，便于测试。"""

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import urlparse

from groupbot.textutil import normalize_domain

logger = logging.getLogger(__name__)

KIND_KEYWORD = "keyword"
KIND_REGEX = "regex"
KIND_LINK = "link"
KIND_NEW_USER_LINK = "new_user_link"
KIND_CHANNEL = "channel"
KIND_FORWARD = "forward"
KIND_FLOOD = "flood"

# 只保留意图明确的引流说法。「加新」「免费」「优惠」这类日常用词不在默认列表里。
DEFAULT_KEYWORDS: tuple[str, ...] = (
    "加群",
    "兼职",
    "刷单",
    "日赚",
    "稳赚",
    "博彩",
    "网赚",
    "代刷",
    "免费领取",
    "点击领取",
    "招商加盟",
    "刷信誉",
)

# 第三条必须写全「微信 / vx / wx」，不能写成「加 + 可选的信/新」，否则「加新」会被删。
DEFAULT_PATTERNS: tuple[str, ...] = (
    r"(?:微信|薇信|威信|微\s*信|wechat|weixin)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{4,}",
    r"(?<![A-Za-z0-9])(?:wx|vx)\s*[:：]\s*[A-Za-z0-9_-]{5,}",
    r"(?<!不)(?:加|添加)\s*(?:我)?\s*(?:的)?\s*(?:微信|薇信|威信|v信|vx|wx|wechat|weixin)",
    r"(?<![A-Za-z0-9])(?:wx|vx|v信)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{4,}",
    r"(?<![A-Za-z0-9])(?:qq|扣扣)\s*[:：]?\s*[1-9][0-9]{4,11}(?!\d)",
)

DEFAULT_WHITELIST: tuple[str, ...] = ("t.me", "telegram.me", "telegram.org")

_URL_RE = re.compile(
    r"(?i)(?:"
    r"(?:https?://|www\.)[^\s<>()\[\]{}\"']+"
    r"|(?<![\w.])(?:t\.me|telegram\.me)/[^\s<>()\[\]{}\"']+"
    r")"
)
_TRAILING = ".,;:!?，。！？、)]}」》>"
_ASCII_WORD = re.compile(r"[A-Za-z0-9_]+")
_MAX_PATTERN_LENGTH = 200


@dataclass(frozen=True)
class TextEntity:
    type: str
    offset: int
    length: int
    url: str | None = None


@dataclass(frozen=True)
class ScanInput:
    text: str = ""
    entities: tuple[TextEntity, ...] = ()
    sender_chat_id: int | None = None
    sender_chat_type: str | None = None
    chat_id: int | None = None
    is_automatic_forward: bool = False
    is_forward: bool = False
    joined_at: datetime | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class Policy:
    enabled: bool = True
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    patterns: tuple[str, ...] = DEFAULT_PATTERNS
    ban_links: bool = True
    whitelist_domains: tuple[str, ...] = DEFAULT_WHITELIST
    new_user_restrict_minutes: int = 30
    ban_channel_messages: bool = False
    ban_forwards: bool = False


@dataclass(frozen=True)
class Verdict:
    violation: bool
    kind: str | None = None
    detail: str | None = None


def keyword_hit(text: str, keyword: str) -> bool:
    word = keyword.strip()
    if not word or not text:
        return False
    if _ASCII_WORD.fullmatch(word):
        return (
            re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])",
                text,
                flags=re.IGNORECASE,
            )
            is not None
        )
    return word.casefold() in text.casefold()


@lru_cache(maxsize=256)
def compile_pattern(pattern: str) -> re.Pattern[str] | None:
    if not pattern or len(pattern) > _MAX_PATTERN_LENGTH:
        logger.warning("忽略过长或空白的正则")
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        logger.warning("忽略无法编译的正则：%s", pattern)
        return None


def _strip_url(raw: str) -> str:
    return raw.strip().rstrip(_TRAILING)


def domain_of(url: str) -> str | None:
    raw = _strip_url(url)
    if not raw:
        return None
    lowered = raw.lower()
    if lowered.startswith("www."):
        raw = "http://" + raw
    elif lowered.startswith("t.me/") or lowered.startswith("telegram.me/"):
        raw = "https://" + raw
    elif "://" not in raw:
        return None
    host = urlparse(raw).hostname
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def domain_allowed(domain: str, whitelist: tuple[str, ...]) -> bool:
    host = domain.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    for item in whitelist:
        allowed = normalize_domain(item)
        if not allowed:
            continue
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def extract_urls(text: str, entities: tuple[TextEntity, ...]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(url: str | None) -> None:
        if not url:
            return
        cleaned = _strip_url(url)
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        found.append(cleaned)

    for entity in entities:
        if entity.type == "text_link":
            add(entity.url)
        elif entity.type == "url" and text:
            add(text[entity.offset : entity.offset + entity.length])
    for match in _URL_RE.finditer(text or ""):
        add(match.group(0))
    return found


def _is_new_user(item: ScanInput, policy: Policy) -> bool:
    if item.joined_at is None or item.now is None or policy.new_user_restrict_minutes <= 0:
        return False
    joined = item.joined_at
    now = item.now
    if joined.tzinfo is None or now.tzinfo is None:
        return False
    return now - joined < timedelta(minutes=policy.new_user_restrict_minutes)


def scan(item: ScanInput, policy: Policy) -> Verdict:
    """匿名管理员和群自己的关联频道转发直接放行，其余按规则检查正文和链接。"""
    if (
        item.sender_chat_id is not None
        and item.chat_id is not None
        and item.sender_chat_id == item.chat_id
    ):
        return Verdict(False)
    if item.is_automatic_forward:
        return Verdict(False)

    foreign_channel = (
        item.sender_chat_type == "channel"
        and item.sender_chat_id is not None
        and item.sender_chat_id != item.chat_id
    )
    if policy.ban_channel_messages and foreign_channel:
        return Verdict(True, KIND_CHANNEL, "频道身份发言")
    if policy.ban_forwards and item.is_forward:
        return Verdict(True, KIND_FORWARD, "转发消息")
    if not policy.enabled:
        return Verdict(False)

    text = item.text or ""
    for keyword in policy.keywords:
        if keyword_hit(text, keyword):
            return Verdict(True, KIND_KEYWORD, keyword.strip())

    for pattern in policy.patterns:
        compiled = compile_pattern(pattern)
        if compiled is not None and compiled.search(text):
            return Verdict(True, KIND_REGEX, pattern)

    urls = extract_urls(text, item.entities)
    blocked = [url for url in urls if not _url_allowed(url, policy.whitelist_domains)]
    if blocked and (policy.ban_links or _is_new_user(item, policy)):
        kind = KIND_LINK if policy.ban_links else KIND_NEW_USER_LINK
        return Verdict(True, kind, blocked[0][:200])
    return Verdict(False)


def _url_allowed(url: str, whitelist: tuple[str, ...]) -> bool:
    domain = domain_of(url)
    if domain is None:
        return False
    return domain_allowed(domain, whitelist)


def scan_parts(
    *,
    text: str | None,
    caption: str | None,
    entities: tuple[TextEntity, ...] = (),
    caption_entities: tuple[TextEntity, ...] = (),
    policy: Policy,
    sender_chat_id: int | None = None,
    sender_chat_type: str | None = None,
    chat_id: int | None = None,
    is_automatic_forward: bool = False,
    is_forward: bool = False,
    joined_at: datetime | None = None,
    now: datetime | None = None,
) -> Verdict:
    """文本消息看 text，媒体消息看 caption。两边的链接实体都要检查。"""
    if text is not None:
        body, used = text, entities
    else:
        body, used = caption or "", caption_entities
    return scan(
        ScanInput(
            text=body,
            entities=used,
            sender_chat_id=sender_chat_id,
            sender_chat_type=sender_chat_type,
            chat_id=chat_id,
            is_automatic_forward=is_automatic_forward,
            is_forward=is_forward,
            joined_at=joined_at,
            now=now,
        ),
        policy,
    )


class FloodTracker:
    """按 (chat_id, user_id) 滑动窗口计数。进程内状态，重启后清零。"""

    def __init__(self) -> None:
        self._hits: dict[tuple[int, int], deque[float]] = {}

    def is_flood(self, chat_id: int, user_id: int, now: float, window: float, limit: int) -> bool:
        if limit <= 0 or window <= 0:
            return False
        key = (chat_id, user_id)
        bucket = self._hits.setdefault(key, deque())
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        bucket.append(now)
        overflow = len(bucket) > limit
        while len(bucket) > limit + 1:
            bucket.popleft()
        if not bucket:
            self._hits.pop(key, None)
        return overflow
