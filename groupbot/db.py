"""按群存储的 SQLite。写操作走 aiosqlite，避免堵住事件循环。"""

import asyncio
import json
import logging
import sqlite3
from datetime import date
from pathlib import Path

import aiosqlite

from groupbot.points import MessagePointState, SignOutcome, apply_message_points, sign_outcome

logger = logging.getLogger(__name__)

LEGACY_DB_ERROR = (
    "检测到旧版数据库（积分没有按群分开）。请把旧库移走，或用 DB_PATH 指定一个新文件。"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    sign_date TEXT,
    sign_streak INTEGER NOT NULL DEFAULT 0,
    total_msgs INTEGER NOT NULL DEFAULT 0,
    msg_points_today INTEGER NOT NULL DEFAULT 0,
    msg_points_date TEXT,
    join_date TEXT,
    warnings INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    welcome_msg TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);

CREATE TABLE IF NOT EXISTS moderation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER,
    actor_id INTEGER,
    kind TEXT NOT NULL,
    message_text TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_modlog_chat ON moderation_log(chat_id, id);

CREATE TABLE IF NOT EXISTS pending_verifications (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_id INTEGER,
    answer TEXT NOT NULL,
    deadline TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
"""


def _loads(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _merge(base: dict, patch: dict) -> dict:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        for statement in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA busy_timeout=5000",
            "PRAGMA foreign_keys=ON",
        ):
            cursor = await self._conn.execute(statement)
            await cursor.fetchall()
        await self._prepare()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("数据库尚未连接")
        return self._conn

    async def _prepare(self) -> None:
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            )
            if await cursor.fetchone():
                info = await self.conn.execute("PRAGMA table_info(users)")
                columns = {row["name"] for row in await info.fetchall()}
                members = await self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='members'"
                )
                if "points" in columns and await members.fetchone() is None:
                    raise RuntimeError(LEGACY_DB_ERROR)
            await self.conn.executescript(SCHEMA)
            version = await self.conn.execute("PRAGMA user_version=2")
            await version.fetchall()
            await self.conn.commit()

    async def _member_unlocked(self, chat_id: int, user_id: int) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT * FROM members WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def _upsert_user(
        self,
        user_id: int,
        username: str | None,
        first_name: str,
        now_iso: str,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                updated_at=excluded.updated_at
            """,
            (user_id, username, first_name, now_iso),
        )

    async def ensure_member(
        self,
        chat_id: int,
        user_id: int,
        username: str | None,
        first_name: str,
        now_iso: str,
        *,
        joined: bool = False,
    ) -> dict:
        async with self._lock:
            await self._upsert_user(user_id, username, first_name, now_iso)
            if joined:
                await self.conn.execute(
                    """
                    INSERT INTO members (chat_id, user_id, join_date)
                    VALUES (?, ?, ?)
                    ON CONFLICT(chat_id, user_id) DO UPDATE SET
                        join_date=COALESCE(members.join_date, excluded.join_date)
                    """,
                    (chat_id, user_id, now_iso),
                )
            else:
                await self.conn.execute(
                    """
                    INSERT INTO members (chat_id, user_id)
                    VALUES (?, ?)
                    ON CONFLICT(chat_id, user_id) DO NOTHING
                    """,
                    (chat_id, user_id),
                )
            await self.conn.commit()
            member = await self._member_unlocked(chat_id, user_id)
            if member is None:
                raise RuntimeError("写入成员记录失败")
            return member

    async def get_member(self, chat_id: int, user_id: int) -> dict | None:
        async with self._lock:
            return await self._member_unlocked(chat_id, user_id)

    async def find_user(self, user_id: int) -> dict | None:
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT user_id, username, first_name FROM users WHERE user_id=?",
                (user_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def find_by_username(self, username: str) -> dict | None:
        async with self._lock:
            cursor = await self.conn.execute(
                """
                SELECT user_id, username, first_name FROM users
                WHERE username = ? COLLATE NOCASE
                """,
                (username,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def sign_in(
        self,
        chat_id: int,
        user_id: int,
        today: date,
        daily: int,
        bonus_days: tuple[int, ...],
    ) -> tuple[SignOutcome, int]:
        async with self._lock:
            member = await self._member_unlocked(chat_id, user_id)
            if member is None:
                raise RuntimeError("成员不存在")
            last = date.fromisoformat(member["sign_date"]) if member["sign_date"] else None
            outcome = sign_outcome(last, member["sign_streak"], today, daily, bonus_days)
            if outcome.already:
                return outcome, int(member["points"])
            await self.conn.execute(
                """
                UPDATE members
                SET sign_date=?, sign_streak=?, points=points+?
                WHERE chat_id=? AND user_id=?
                """,
                (today.isoformat(), outcome.streak, outcome.awarded, chat_id, user_id),
            )
            await self.conn.commit()
            updated = await self._member_unlocked(chat_id, user_id)
            return outcome, int(updated["points"] if updated else outcome.awarded)

    async def add_points(self, chat_id: int, user_id: int, delta: int) -> int:
        async with self._lock:
            await self.conn.execute(
                "UPDATE members SET points = points + ? WHERE chat_id=? AND user_id=?",
                (delta, chat_id, user_id),
            )
            await self.conn.commit()
            member = await self._member_unlocked(chat_id, user_id)
            return int(member["points"]) if member else 0

    async def try_spend(self, chat_id: int, user_id: int, cost: int) -> bool:
        if cost <= 0:
            return True
        async with self._lock:
            cursor = await self.conn.execute(
                """
                UPDATE members SET points = points - ?
                WHERE chat_id=? AND user_id=? AND points >= ?
                """,
                (cost, chat_id, user_id, cost),
            )
            await self.conn.commit()
            return cursor.rowcount == 1

    async def record_message(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        today: date,
        msg_per_point: int,
        max_per_day: int,
        now_iso: str,
        max_length: int,
    ) -> int:
        async with self._lock:
            member = await self._member_unlocked(chat_id, user_id)
            if member is None:
                return 0
            points_date = (
                date.fromisoformat(member["msg_points_date"]) if member["msg_points_date"] else None
            )
            state = MessagePointState(member["total_msgs"], member["msg_points_today"], points_date)
            new_state, gained = apply_message_points(state, today, msg_per_point, max_per_day)
            await self.conn.execute(
                """
                UPDATE members
                SET total_msgs=?, msg_points_today=?, msg_points_date=?, points=points+?
                WHERE chat_id=? AND user_id=?
                """,
                (
                    new_state.total_msgs,
                    new_state.points_today,
                    today.isoformat(),
                    gained,
                    chat_id,
                    user_id,
                ),
            )
            snippet = text.strip()[:max_length]
            if snippet:
                await self.conn.execute(
                    """
                    INSERT INTO messages (chat_id, user_id, message_text, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (chat_id, user_id, snippet, now_iso),
                )
            await self.conn.commit()
            return gained

    async def set_warnings(self, chat_id: int, user_id: int, count: int) -> int:
        count = max(0, count)
        async with self._lock:
            await self.conn.execute(
                "UPDATE members SET warnings=? WHERE chat_id=? AND user_id=?",
                (count, chat_id, user_id),
            )
            await self.conn.commit()
            return count

    async def ranking(self, chat_id: int, limit: int = 10) -> list[dict]:
        async with self._lock:
            cursor = await self.conn.execute(
                """
                SELECT m.user_id, u.username, u.first_name, m.points, m.total_msgs
                FROM members m
                LEFT JOIN users u ON u.user_id = m.user_id
                WHERE m.chat_id=?
                ORDER BY m.points DESC, m.total_msgs DESC, m.user_id ASC
                LIMIT ?
                """,
                (chat_id, limit),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def ensure_group(self, chat_id: int, title: str | None, now_iso: str) -> dict:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO groups (chat_id, title, settings, created_at)
                VALUES (?, ?, '{}', ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title=COALESCE(excluded.title, groups.title)
                """,
                (chat_id, title, now_iso),
            )
            await self.conn.commit()
            return await self._group_unlocked(chat_id)

    async def _group_unlocked(self, chat_id: int) -> dict:
        cursor = await self.conn.execute("SELECT * FROM groups WHERE chat_id=?", (chat_id,))
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("群记录丢失")
        return dict(row)

    async def get_group(self, chat_id: int) -> dict | None:
        async with self._lock:
            cursor = await self.conn.execute("SELECT * FROM groups WHERE chat_id=?", (chat_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def set_welcome(self, chat_id: int, welcome: str | None) -> None:
        async with self._lock:
            await self.conn.execute(
                "UPDATE groups SET welcome_msg=? WHERE chat_id=?",
                (welcome, chat_id),
            )
            await self.conn.commit()

    async def patch_settings(self, chat_id: int, patch: dict) -> dict:
        async with self._lock:
            current = await self._group_unlocked(chat_id)
            merged = _merge(_loads(current["settings"]), patch)
            await self.conn.execute(
                "UPDATE groups SET settings=? WHERE chat_id=?",
                (json.dumps(merged, ensure_ascii=False), chat_id),
            )
            await self.conn.commit()
            return merged

    async def log_action(
        self,
        *,
        chat_id: int,
        user_id: int | None,
        actor_id: int | None,
        kind: str,
        action: str,
        detail: str | None,
        message_text: str | None,
        created_at: str,
        max_length: int,
    ) -> None:
        snippet = (message_text or "").strip()[:max_length] or None
        note = (detail or "").strip()[:500] or None
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO moderation_log (
                    chat_id, user_id, actor_id, kind, message_text, action, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (chat_id, user_id, actor_id, kind, snippet, action, note, created_at),
            )
            await self.conn.commit()

    async def list_actions(self, chat_id: int, limit: int) -> list[dict]:
        async with self._lock:
            cursor = await self.conn.execute(
                """
                SELECT * FROM moderation_log
                WHERE chat_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (chat_id, limit),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def group_stats(self, chat_id: int) -> dict[str, int]:
        async with self._lock:
            members = await self.conn.execute(
                "SELECT COUNT(*) AS n FROM members WHERE chat_id=?",
                (chat_id,),
            )
            messages = await self.conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE chat_id=?",
                (chat_id,),
            )
            violations = await self.conn.execute(
                "SELECT COUNT(*) AS n FROM moderation_log WHERE chat_id=?",
                (chat_id,),
            )
            return {
                "members": int((await members.fetchone())["n"]),
                "messages": int((await messages.fetchone())["n"]),
                "actions": int((await violations.fetchone())["n"]),
            }

    async def purge_messages(self, cutoff_iso: str, max_rows: int) -> None:
        async with self._lock:
            await self.conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff_iso,))
            await self.conn.execute(
                """
                DELETE FROM messages WHERE id NOT IN (
                    SELECT id FROM messages ORDER BY id DESC LIMIT ?
                )
                """,
                (max_rows,),
            )
            await self.conn.commit()

    async def count_messages(self) -> int:
        async with self._lock:
            cursor = await self.conn.execute("SELECT COUNT(*) AS n FROM messages")
            return int((await cursor.fetchone())["n"])

    async def save_pending(
        self,
        chat_id: int,
        user_id: int,
        message_id: int | None,
        answer: str,
        deadline: str,
    ) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO pending_verifications (chat_id, user_id, message_id, answer, deadline)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    message_id=excluded.message_id,
                    answer=excluded.answer,
                    deadline=excluded.deadline
                """,
                (chat_id, user_id, message_id, answer, deadline),
            )
            await self.conn.commit()

    async def get_pending(self, chat_id: int, user_id: int) -> dict | None:
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT * FROM pending_verifications WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def delete_pending(self, chat_id: int, user_id: int) -> None:
        async with self._lock:
            await self.conn.execute(
                "DELETE FROM pending_verifications WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            await self.conn.commit()

    async def list_pending(self) -> list[dict]:
        async with self._lock:
            cursor = await self.conn.execute("SELECT * FROM pending_verifications")
            return [dict(row) for row in await cursor.fetchall()]

    async def backup(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)

        def _copy() -> None:
            source = sqlite3.connect(self.path)
            try:
                target = sqlite3.connect(dest)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()

        await asyncio.to_thread(_copy)
        logger.info("数据库已备份到 %s", dest)
