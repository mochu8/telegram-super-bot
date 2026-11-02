import re
from datetime import UTC, datetime, timedelta

from groupbot.moderation import (
    DEFAULT_KEYWORDS,
    DEFAULT_PATTERNS,
    FloodTracker,
    Policy,
    ScanInput,
    TextEntity,
    keyword_hit,
    scan,
    scan_parts,
)

OLD_PATTERN = re.compile(r"加[我]?[\s]?[微维]?[信新]")


def policy(**kwargs) -> Policy:
    return Policy(**kwargs)


def test_old_pattern_matched_ordinary_chat_and_the_new_one_does_not():
    assert OLD_PATTERN.search("加新")
    assert OLD_PATTERN.search("今天加新朋友")
    verdict = scan(ScanInput("今天加新朋友，一起去吃饭"), policy())
    assert verdict.violation is False
    assert scan(ScanInput("加新"), policy()).violation is False


def test_solicitation_is_caught():
    for text in ("加微信 abcde", "加我微信", "加我vx", "wx:hello1", "QQ 123456", "微信: abcde"):
        verdict = scan(ScanInput(text), policy())
        assert verdict.violation, text
        assert verdict.kind == "regex"


def test_common_words_are_not_default_keywords():
    for word in ("免费", "优惠", "折扣", "代理", "广告", "推广", "赚钱", "领取"):
        assert word not in DEFAULT_KEYWORDS
    for text in ("这个免费吗", "有优惠吗", "今天领取快递", "代理服务器怎么配"):
        assert scan(ScanInput(text), policy()).violation is False


def test_keywords_are_case_insensitive_and_ascii_uses_boundaries():
    rules = policy(keywords=("VPN", "加群"), patterns=())
    assert keyword_hit("看看这个 Vpn", "VPN")
    assert scan(ScanInput("看看这个 Vpn"), rules).kind == "keyword"
    assert scan(ScanInput("vpns 不是这个词"), rules).violation is False
    assert scan(ScanInput("快来加群"), rules).kind == "keyword"
    assert scan(ScanInput("加入群聊"), rules).violation is False


def test_whitelist_is_by_domain_not_substring():
    rules = policy(
        patterns=(),
        keywords=(),
        ban_links=True,
        whitelist_domains=("t.me/", "telegram.org"),
    )
    assert scan(ScanInput("看这个 https://t.me/group"), rules).violation is False
    assert scan(ScanInput("文档 https://core.telegram.org/bots"), rules).violation is False
    hidden = scan(ScanInput("看这个 https://evil.com/path?q=t.me/"), rules)
    assert hidden.violation
    assert hidden.kind == "link"
    assert scan(ScanInput("https://nottelegram.org/a"), rules).kind == "link"


def test_text_link_entity_and_caption_are_scanned():
    entity = TextEntity(type="text_link", offset=0, length=2, url="https://spam.example/a")
    verdict = scan_parts(
        text=None,
        caption="看图",
        caption_entities=(entity,),
        policy=policy(patterns=(), keywords=()),
    )
    assert verdict.kind == "link"

    forwarded = scan_parts(
        text="加微信 spamuser",
        caption=None,
        policy=policy(),
        is_forward=True,
    )
    assert forwarded.kind == "regex"


def test_channel_forward_and_anonymous_admin():
    channel = scan(
        ScanInput(text="大家好", sender_chat_id=99, sender_chat_type="channel", chat_id=1),
        policy(ban_channel_messages=True, patterns=(), keywords=()),
    )
    assert channel.kind == "channel"
    anonymous = scan(
        ScanInput(text="加群", sender_chat_id=1, sender_chat_type="group", chat_id=1),
        policy(),
    )
    assert anonymous.violation is False
    linked = scan(
        ScanInput(
            text="加群",
            sender_chat_id=99,
            sender_chat_type="channel",
            chat_id=1,
            is_automatic_forward=True,
        ),
        policy(ban_channel_messages=True),
    )
    assert linked.violation is False


def test_new_user_links_respect_whitelist_and_the_window():
    now = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    joined = now - timedelta(minutes=5)
    rules = policy(ban_links=False, patterns=(), keywords=(), new_user_restrict_minutes=30)
    blocked = scan(
        ScanInput("https://example.com/a", joined_at=joined, now=now),
        rules,
    )
    assert blocked.kind == "new_user_link"
    allowed = scan(
        ScanInput("https://t.me/group", joined_at=joined, now=now),
        rules,
    )
    assert allowed.violation is False
    older = scan(
        ScanInput(
            "https://example.com/a",
            joined_at=now - timedelta(minutes=31),
            now=now,
        ),
        rules,
    )
    assert older.violation is False


def test_invalid_regex_does_not_raise():
    assert scan(ScanInput("hello"), policy(patterns=("[",), keywords=())).violation is False


def test_default_patterns_are_the_ones_we_ship():
    assert DEFAULT_PATTERNS
    assert all("加新" not in pattern for pattern in DEFAULT_PATTERNS)


def test_flood_tracker_is_per_user_and_windowed():
    tracker = FloodTracker()
    now = 1000.0
    assert tracker.is_flood(1, 2, now, window=10, limit=3) is False
    assert tracker.is_flood(1, 2, now + 1, 10, 3) is False
    assert tracker.is_flood(1, 2, now + 2, 10, 3) is False
    assert tracker.is_flood(1, 2, now + 3, 10, 3) is True
    assert tracker.is_flood(1, 3, now + 3, 10, 3) is False
    assert tracker.is_flood(2, 2, now + 3, 10, 3) is False
    assert tracker.is_flood(1, 2, now + 30, 10, 3) is False
