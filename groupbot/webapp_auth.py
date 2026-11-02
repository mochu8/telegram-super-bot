"""校验 Telegram Mini App 的 initData。每次接口调用都要过一遍。"""

import hashlib
import hmac
import json
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import parse_qsl


class AuthError(Exception):
    def __init__(self, public: str) -> None:
        super().__init__(public)
        self.public = public


@dataclass(frozen=True)
class WebAppIdentity:
    user_id: int
    username: str | None
    first_name: str
    start_param: str | None
    auth_date: int


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: int | None = None,
    max_age: int = 86400,
) -> WebAppIdentity:
    if not init_data or not bot_token:
        raise AuthError("缺少登录数据。")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise AuthError("登录数据无效。") from exc
    data = dict(pairs)
    received = data.pop("hash", None)
    if not received or len(received) != 64:
        raise AuthError("登录数据无效。")
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received):
        raise AuthError("登录数据无效。")
    try:
        auth_date = int(data.get("auth_date", ""))
    except ValueError as exc:
        raise AuthError("登录数据无效。") from exc
    current = int(time.time()) if now is None else now
    if auth_date > current + 60:
        raise AuthError("登录数据无效。")
    if current - auth_date > max_age:
        raise AuthError("登录已过期，请重新打开面板。")
    try:
        user = json.loads(data.get("user", ""))
    except json.JSONDecodeError as exc:
        raise AuthError("登录数据无效。") from exc
    if not isinstance(user, dict) or isinstance(user.get("id"), bool):
        raise AuthError("登录数据无效。")
    user_id = user.get("id")
    if not isinstance(user_id, int):
        raise AuthError("登录数据无效。")
    first_name = user.get("first_name")
    username = user.get("username")
    start_param = data.get("start_param") or None
    return WebAppIdentity(
        user_id=user_id,
        username=username if isinstance(username, str) else None,
        first_name=first_name if isinstance(first_name, str) and first_name else "用户",
        start_param=start_param if isinstance(start_param, str) else None,
        auth_date=auth_date,
    )


class SlidingLimiter:
    """按键滑动窗口。面板用它限制每个用户和每个 IP。"""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float, limit: int, window: float) -> bool:
        if limit <= 0 or window <= 0:
            return False
        bucket = self._hits.setdefault(key, deque())
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        if len(self._hits) > 10000:
            stale = [
                name
                for name, items in self._hits.items()
                if not items or now - items[-1] > window
            ]
            for name in stale:
                self._hits.pop(name, None)
        return True
