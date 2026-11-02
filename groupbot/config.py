"""从环境变量和 config.json 读取配置。环境变量优先。"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from groupbot.groupsettings import (
    Announcement,
    AutoReply,
    WelcomeButton,
    announcements_from,
    buttons_from,
    replies_from,
)
from groupbot.moderation import DEFAULT_KEYWORDS, DEFAULT_PATTERNS, DEFAULT_WHITELIST
from groupbot.textutil import normalize_domain

PLACEHOLDER_TOKEN = "YOUR_BOT_TOKEN_HERE"
DEFAULT_WELCOME = "🎉 欢迎 {name} 加入 {chat}！\n\n请遵守群规，文明交流。"


class ConfigError(Exception):
    """配置缺失或无法解析。"""


@dataclass(frozen=True)
class AppConfig:
    token: str
    admin_ids: frozenset[int]
    timezone_name: str
    db_path: str
    log_channel_id: int | None
    welcome_message: str
    keywords: tuple[str, ...]
    regex_patterns: tuple[str, ...]
    anti_ad_enabled: bool
    ban_links: bool
    whitelist_domains: tuple[str, ...]
    new_user_restrict_minutes: int
    mute_duration: int
    ban_channel_messages: bool
    ban_forwards: bool
    delete_service: bool
    delete_leave: bool
    delete_delay: int
    warning_delete_delay: int
    sign_daily: int
    sign_bonus: tuple[int, ...]
    msg_per_point: int
    max_points_per_day: int
    dice_cost: int
    dice_reward: int
    dice_reward_threshold: int
    backup_enabled: bool
    backup_interval_hours: int
    backup_keep: int
    backup_dir: str
    verify_enabled: bool
    verify_timeout: int
    verify_mode: str
    flood_enabled: bool
    flood_max: int
    flood_window: int
    flood_mute: int
    warn_threshold: int
    message_retention_days: int
    message_max_rows: int
    message_max_length: int
    panel_base_url: str
    panel_host: str
    panel_port: int
    panel_auth_max_age: int
    panel_rate_limit: int
    panel_rate_window: int

    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


@dataclass(frozen=True)
class EffectiveSettings:
    welcome_message: str
    anti_ad_enabled: bool
    keywords: tuple[str, ...]
    regex_patterns: tuple[str, ...]
    ban_links: bool
    whitelist_domains: tuple[str, ...]
    new_user_restrict_minutes: int
    mute_duration: int
    ban_channel_messages: bool
    ban_forwards: bool
    delete_service: bool
    delete_leave: bool
    delete_delay: int
    warning_delete_delay: int
    verify_enabled: bool
    verify_timeout: int
    verify_mode: str
    flood_enabled: bool
    flood_max: int
    flood_window: int
    flood_mute: int
    warn_threshold: int
    log_channel_id: int | None
    sign_daily: int
    sign_bonus: tuple[int, ...]
    msg_per_point: int
    max_points_per_day: int
    dice_cost: int
    dice_reward: int
    dice_reward_threshold: int
    message_retention_days: int
    message_max_rows: int
    message_max_length: int
    timezone_name: str
    welcome_enabled: bool
    welcome_buttons: tuple[WelcomeButton, ...]
    auto_reply_enabled: bool
    auto_replies: tuple[AutoReply, ...]
    announce_enabled: bool
    announcements: tuple[Announcement, ...]
    rules_enabled: bool
    rules_text: str
    points_enabled: bool


def backup_interval_hours(cfg: AppConfig) -> int | None:
    if not cfg.backup_enabled or cfg.backup_interval_hours <= 0:
        return None
    return cfg.backup_interval_hours


def _section(data: Mapping[str, object], key: str) -> dict[str, object]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{key} 必须是对象。")
    return value


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ConfigError("布尔配置只能是 true 或 false。")


def _as_int(value: object, default: int, *, minimum: int | None = None) -> int:
    if value is None:
        result = default
    elif isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("数字配置必须是整数。")
    else:
        result = value
    if minimum is not None and result < minimum:
        raise ConfigError(f"数字配置不能小于 {minimum}。")
    return result


def _as_str(value: object, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError("文本配置必须是字符串。")
    return value


def _as_str_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError("列表配置必须是字符串数组。")
    return tuple(item.strip() for item in value if item.strip())


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_int_tuple(value: object, default: tuple[int, ...]) -> tuple[int, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or not all(_plain_int(item) for item in value):
        raise ConfigError("数字列表配置必须是整数数组。")
    return tuple(value)


def _as_admin_ids(value: object) -> frozenset[int]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ConfigError("admin_ids 必须是数字 ID 列表。")
    ids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ConfigError("admin_ids 必须是数字 ID 列表。")
        ids.append(item)
    return frozenset(ids)


def _as_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("日志频道 ID 必须是整数。")
    return value


def _whitelist(value: object) -> tuple[str, ...]:
    raw = _as_str_tuple(value, DEFAULT_WHITELIST)
    domains: list[str] = []
    for item in raw:
        domain = normalize_domain(item)
        domains.append(domain or item.strip().lower().rstrip("/"))
    return tuple(dict.fromkeys(domains))


def build_config(file_data: Mapping[str, object] | None, environ: Mapping[str, str]) -> AppConfig:
    data = dict(file_data or {})
    anti = _section(data, "anti_ad")
    auto_delete = _section(data, "auto_delete")
    points = _section(data, "points")
    games = _section(data, "games")
    backup = _section(data, "backup")
    verification = _section(data, "verification")
    flood = _section(data, "anti_flood")
    warnings = _section(data, "warnings")
    messages = _section(data, "messages")

    token = environ.get("BOT_TOKEN", "").strip() or _as_str(data.get("bot_token"), "")
    admin_raw = environ.get("ADMIN_IDS", "").strip()
    if admin_raw:
        try:
            admin_ids = frozenset(
                int(part.strip()) for part in admin_raw.split(",") if part.strip()
            )
        except ValueError as exc:
            raise ConfigError("环境变量 ADMIN_IDS 必须是逗号分隔的数字 ID。") from exc
    else:
        admin_ids = _as_admin_ids(data.get("admin_ids"))

    log_raw = environ.get("LOG_CHANNEL_ID", "").strip()
    if log_raw:
        try:
            log_channel_id: int | None = int(log_raw)
        except ValueError as exc:
            raise ConfigError("环境变量 LOG_CHANNEL_ID 必须是整数。") from exc
    else:
        log_channel_id = _as_optional_int(data.get("log_channel_id"))

    timezone_name = environ.get("TZ", "").strip() or _as_str(data.get("timezone"), "Asia/Shanghai")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"未知时区：{timezone_name}") from exc

    verify_mode = _as_str(verification.get("mode"), "button")
    if verify_mode not in {"button", "captcha"}:
        raise ConfigError("verification.mode 只能是 button 或 captcha。")

    return AppConfig(
        token=token,
        admin_ids=admin_ids,
        timezone_name=timezone_name,
        db_path=environ.get("DB_PATH", "").strip() or _as_str(data.get("database"), "data/bot.db"),
        log_channel_id=log_channel_id,
        welcome_message=_as_str(data.get("welcome_message"), DEFAULT_WELCOME),
        keywords=_as_str_tuple(anti.get("keywords"), DEFAULT_KEYWORDS),
        regex_patterns=_as_str_tuple(anti.get("regex_patterns"), DEFAULT_PATTERNS),
        anti_ad_enabled=_as_bool(anti.get("enabled"), True),
        ban_links=_as_bool(anti.get("ban_links"), True),
        whitelist_domains=_whitelist(anti.get("whitelist_links", anti.get("whitelist_domains"))),
        new_user_restrict_minutes=_as_int(anti.get("new_user_restrict"), 30, minimum=0),
        mute_duration=_as_int(anti.get("mute_duration"), 3600, minimum=0),
        ban_channel_messages=_as_bool(anti.get("ban_channel_messages"), False),
        ban_forwards=_as_bool(anti.get("ban_forwards"), False),
        delete_service=_as_bool(auto_delete.get("service_msg"), True),
        delete_leave=_as_bool(auto_delete.get("leave_msg"), True),
        delete_delay=_as_int(auto_delete.get("delay_seconds"), 60, minimum=0),
        warning_delete_delay=_as_int(auto_delete.get("warning_seconds"), 10, minimum=0),
        sign_daily=_as_int(points.get("sign_daily"), 10, minimum=0),
        sign_bonus=_as_int_tuple(points.get("sign_bonus"), (7, 30, 100, 365)),
        msg_per_point=_as_int(points.get("msg_per_point"), 50, minimum=0),
        max_points_per_day=_as_int(points.get("max_points_per_day"), 100, minimum=0),
        dice_cost=_as_int(games.get("dice_cost"), 5, minimum=0),
        dice_reward=_as_int(games.get("dice_reward"), 10, minimum=0),
        dice_reward_threshold=_as_int(games.get("dice_reward_threshold"), 5, minimum=1),
        backup_enabled=_as_bool(backup.get("enabled"), True),
        backup_interval_hours=_as_int(backup.get("interval_hours"), 24, minimum=0),
        backup_keep=_as_int(backup.get("keep"), 7, minimum=1),
        backup_dir=environ.get("BACKUP_DIR", "").strip() or _as_str(backup.get("dir"), "backups"),
        verify_enabled=_as_bool(verification.get("enabled"), True),
        verify_timeout=_as_int(verification.get("timeout_seconds"), 120, minimum=15),
        verify_mode=verify_mode,
        flood_enabled=_as_bool(flood.get("enabled"), True),
        flood_max=_as_int(flood.get("max_messages"), 5, minimum=2),
        flood_window=_as_int(flood.get("window_seconds"), 8, minimum=2),
        flood_mute=_as_int(flood.get("mute_seconds"), 60, minimum=0),
        warn_threshold=_as_int(warnings.get("threshold"), 3, minimum=1),
        message_retention_days=_as_int(messages.get("retention_days"), 7, minimum=1),
        message_max_rows=_as_int(messages.get("max_rows"), 50000, minimum=100),
        message_max_length=_as_int(messages.get("max_length"), 200, minimum=20),
        panel_base_url=_panel_base_url(environ.get("PANEL_BASE_URL", "")),
        panel_host=_panel_host(environ.get("PANEL_HOST", "")),
        panel_port=_panel_port(environ.get("PANEL_PORT", "")),
        panel_auth_max_age=_bounded_env_int(
            environ.get("PANEL_AUTH_MAX_AGE", ""),
            86400,
            60,
            7 * 86400,
            "PANEL_AUTH_MAX_AGE",
        ),
        panel_rate_limit=_bounded_env_int(
            environ.get("PANEL_RATE_LIMIT", ""),
            60,
            1,
            600,
            "PANEL_RATE_LIMIT",
        ),
        panel_rate_window=_bounded_env_int(
            environ.get("PANEL_RATE_WINDOW", ""),
            60,
            1,
            3600,
            "PANEL_RATE_WINDOW",
        ),
    )


def _panel_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith("https://") or any(char.isspace() for char in value):
        raise ConfigError("PANEL_BASE_URL 必须是 https 地址。")
    return value


def _panel_host(raw: str) -> str:
    value = raw.strip() or "0.0.0.0"
    if any(char.isspace() for char in value):
        raise ConfigError("PANEL_HOST 不正确。")
    return value


def _panel_port(raw: str) -> int:
    return _bounded_env_int(raw, 8080, 1, 65535, "PANEL_PORT")


def _bounded_env_int(raw: str, default: int, low: int, high: int, name: str) -> int:
    text = raw.strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数。") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} 超出范围。")
    return value


def load_file(path: Path) -> dict[str, object]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件格式错误：{exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError("配置文件必须是 JSON 对象。")
    return loaded


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    env = os.environ if environ is None else environ
    path = Path(env.get("CONFIG_PATH", "config.json"))
    file_data: dict[str, object] | None = None
    if path.is_file():
        file_data = load_file(path)
    cfg = build_config(file_data, env)
    if not cfg.token or cfg.token == PLACEHOLDER_TOKEN:
        raise ConfigError("请设置环境变量 BOT_TOKEN，或在 config.json 中填写 bot_token。")
    return cfg


def _flag(overrides: Mapping[str, object], key: str, default: bool) -> bool:
    if key not in overrides or not isinstance(overrides[key], bool):
        return default
    return overrides[key]


def _number(
    overrides: Mapping[str, object],
    key: str,
    default: int,
    low: int,
    high: int,
) -> int:
    value = overrides.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(value, low), high)


def effective_exact(
    base: tuple[str, ...],
    extra: tuple[str, ...],
    disabled: tuple[str, ...],
) -> tuple[str, ...]:
    blocked = set(disabled)
    seen: set[str] = set()
    chosen: list[str] = []
    for item in (*base, *extra):
        if item in blocked or item in seen:
            continue
        seen.add(item)
        chosen.append(item)
    return tuple(chosen)


def _bonus_days(overrides: Mapping[str, object], default: tuple[int, ...]) -> tuple[int, ...]:
    if "sign_bonus" not in overrides:
        return default
    value = overrides["sign_bonus"]
    if not isinstance(value, list) or not all(_plain_int(item) for item in value):
        return default
    return tuple(item for item in value if 1 <= item <= 3650)


def _rules_text(overrides: Mapping[str, object]) -> str:
    value = overrides.get("rules_text", "")
    if not isinstance(value, str):
        return ""
    return value[:3500]


def _str_list(overrides: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = overrides.get(key, ())
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def effective_keywords(
    base: tuple[str, ...],
    extra: tuple[str, ...],
    disabled: tuple[str, ...],
) -> tuple[str, ...]:
    blocked = {item.casefold() for item in disabled}
    seen: set[str] = set()
    chosen: list[str] = []
    for word in (*base, *extra):
        folded = word.casefold()
        if folded in blocked or folded in seen:
            continue
        seen.add(folded)
        chosen.append(word)
    return tuple(chosen)


def effective_domains(
    base: tuple[str, ...],
    extra: tuple[str, ...],
    disabled: tuple[str, ...],
) -> tuple[str, ...]:
    def norm(item: str) -> str:
        return normalize_domain(item) or item.strip().lower()

    blocked = {norm(item) for item in disabled}
    seen: set[str] = set()
    chosen: list[str] = []
    for item in (*base, *extra):
        domain = norm(item)
        if not domain or domain in blocked or domain in seen:
            continue
        seen.add(domain)
        chosen.append(domain)
    return tuple(chosen)


def resolve_log_channel(cfg: AppConfig, overrides: Mapping[str, object]) -> int | None:
    if "log_channel_id" not in overrides:
        return cfg.log_channel_id
    value = overrides["log_channel_id"]
    if value in (None, 0, "0", "off"):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return cfg.log_channel_id


def resolve_settings(
    cfg: AppConfig,
    overrides: Mapping[str, object] | None = None,
    welcome: str | None = None,
) -> EffectiveSettings:
    group = overrides or {}
    mode = group.get("verify_mode", cfg.verify_mode)
    if mode not in {"button", "captcha"}:
        mode = cfg.verify_mode
    return EffectiveSettings(
        welcome_message=welcome or cfg.welcome_message,
        anti_ad_enabled=_flag(group, "anti_ad_enabled", cfg.anti_ad_enabled),
        keywords=effective_keywords(
            cfg.keywords,
            _str_list(group, "extra_keywords"),
            _str_list(group, "disabled_keywords"),
        ),
        regex_patterns=effective_exact(
            cfg.regex_patterns,
            _str_list(group, "extra_patterns"),
            _str_list(group, "disabled_patterns"),
        ),
        ban_links=_flag(group, "ban_links", cfg.ban_links),
        whitelist_domains=effective_domains(
            cfg.whitelist_domains,
            _str_list(group, "extra_whitelist"),
            _str_list(group, "disabled_whitelist"),
        ),
        new_user_restrict_minutes=_number(
            group, "new_user_restrict_minutes", cfg.new_user_restrict_minutes, 0, 10080
        ),
        mute_duration=_number(group, "mute_duration", cfg.mute_duration, 0, 366 * 24 * 3600),
        ban_channel_messages=_flag(group, "ban_channel_messages", cfg.ban_channel_messages),
        ban_forwards=_flag(group, "ban_forwards", cfg.ban_forwards),
        delete_service=_flag(group, "delete_service", cfg.delete_service),
        delete_leave=_flag(group, "delete_leave", cfg.delete_leave),
        delete_delay=cfg.delete_delay,
        warning_delete_delay=cfg.warning_delete_delay,
        verify_enabled=_flag(group, "verify_enabled", cfg.verify_enabled),
        verify_timeout=_number(group, "verify_timeout", cfg.verify_timeout, 15, 3600),
        verify_mode=str(mode),
        flood_enabled=_flag(group, "flood_enabled", cfg.flood_enabled),
        flood_max=_number(group, "flood_max", cfg.flood_max, 2, 50),
        flood_window=_number(group, "flood_window", cfg.flood_window, 2, 300),
        flood_mute=_number(group, "flood_mute", cfg.flood_mute, 30, 86400),
        warn_threshold=_number(group, "warn_threshold", cfg.warn_threshold, 1, 20),
        log_channel_id=resolve_log_channel(cfg, group),
        sign_daily=_number(group, "sign_daily", cfg.sign_daily, 0, 10000),
        sign_bonus=_bonus_days(group, cfg.sign_bonus),
        msg_per_point=_number(group, "msg_per_point", cfg.msg_per_point, 0, 10000),
        max_points_per_day=_number(group, "max_points_per_day", cfg.max_points_per_day, 0, 100000),
        dice_cost=_number(group, "dice_cost", cfg.dice_cost, 0, 100000),
        dice_reward=_number(group, "dice_reward", cfg.dice_reward, 0, 100000),
        dice_reward_threshold=_number(
            group, "dice_reward_threshold", cfg.dice_reward_threshold, 1, 6
        ),
        message_retention_days=cfg.message_retention_days,
        message_max_rows=cfg.message_max_rows,
        message_max_length=cfg.message_max_length,
        timezone_name=cfg.timezone_name,
        welcome_enabled=_flag(group, "welcome_enabled", True),
        welcome_buttons=buttons_from(dict(group)),
        auto_reply_enabled=_flag(group, "auto_reply_enabled", False),
        auto_replies=replies_from(dict(group)),
        announce_enabled=_flag(group, "announce_enabled", False),
        announcements=announcements_from(dict(group)),
        rules_enabled=_flag(group, "rules_enabled", True),
        rules_text=_rules_text(group),
        points_enabled=_flag(group, "points_enabled", True),
    )
