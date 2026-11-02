import asyncio
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from groupbot.db import Database
from groupbot.timeutil import isoformat, utc_now


def run(coro):
    return asyncio.run(coro)


async def open_db(path: Path) -> Database:
    database = Database(str(path))
    await database.connect()
    return database


def test_points_sign_and_warnings_are_per_chat(tmp_path):
    async def check():
        database = await open_db(tmp_path / "bot.db")
        try:
            now = isoformat(utc_now())
            await database.ensure_member(1, 7, "Alice", "甲", now)
            await database.ensure_member(2, 7, "Alice", "甲", now)
            today = date(2026, 5, 1)
            first, points_one = await database.sign_in(1, 7, today, 10, (7,))
            second, points_two = await database.sign_in(2, 7, today, 10, (7,))
            repeat, _points = await database.sign_in(1, 7, today, 10, (7,))
            assert first.already is False
            assert second.already is False
            assert points_one == 10
            assert points_two == 10
            assert repeat.already is True

            await database.set_warnings(1, 7, 2)
            assert (await database.get_member(1, 7))["warnings"] == 2
            assert (await database.get_member(2, 7))["warnings"] == 0

            await database.add_points(1, 7, 5)
            ranking = await database.ranking(1, 10)
            assert [row["user_id"] for row in ranking] == [7]
            assert ranking[0]["points"] == 15
            assert await database.ranking(2, 10)
            assert (await database.ranking(2, 10))[0]["points"] == 10

            found = await database.find_by_username("alice")
            assert found is not None
            assert found["user_id"] == 7
            assert await database.try_spend(1, 7, 100) is False
            assert await database.try_spend(1, 7, 15) is True
            assert (await database.get_member(1, 7))["points"] == 0
            assert (await database.get_member(2, 7))["points"] == 10
        finally:
            await database.close()

    run(check())


def test_message_log_is_truncated_and_purged(tmp_path):
    async def check():
        database = await open_db(tmp_path / "bot.db")
        try:
            now = isoformat(utc_now())
            await database.ensure_member(1, 7, None, "甲", now)
            await database.record_message(
                1,
                7,
                "字" * 500,
                date(2026, 5, 1),
                50,
                100,
                "2020-01-01T00:00:00+00:00",
                20,
            )
            for index in range(4):
                await database.record_message(
                    1,
                    7,
                    f"new-{index}",
                    date(2026, 5, 2),
                    50,
                    100,
                    f"2026-05-02T00:00:0{index}+00:00",
                    20,
                )
            cursor = await database.conn.execute("SELECT message_text FROM messages ORDER BY id")
            stored = [row["message_text"] for row in await cursor.fetchall()]
            assert stored[0] == "字" * 20
            await database.purge_messages("2021-01-01T00:00:00+00:00", 2)
            assert await database.count_messages() == 2
        finally:
            await database.close()

    run(check())


def test_backup_and_legacy_database(tmp_path):
    async def check_backup():
        database = await open_db(tmp_path / "bot.db")
        try:
            await database.ensure_member(1, 7, "a", "甲", isoformat(utc_now()))
            destination = tmp_path / "backups" / "bot.db"
            await database.backup(destination)
        finally:
            await database.close()
        copied = sqlite3.connect(destination)
        count = copied.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        copied.close()
        assert count == 1

    run(check_backup())

    legacy = tmp_path / "legacy.db"
    connection = sqlite3.connect(legacy)
    connection.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, points INTEGER)")
    connection.commit()
    connection.close()
    database = Database(str(legacy))
    with pytest.raises(RuntimeError, match="旧版数据库"):
        run(database.connect())
    run(database.close())
