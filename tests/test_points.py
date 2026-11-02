import hashlib
from datetime import date, timedelta

from groupbot.points import (
    FORTUNES,
    MessagePointState,
    apply_message_points,
    daily_fortune,
    sign_outcome,
)


def test_sign_streak_bonus_and_repeat():
    today = date(2026, 5, 8)
    first = sign_outcome(None, 0, today, 10, (7, 30))
    assert first.already is False
    assert first.streak == 1
    assert first.awarded == 10
    assert first.bonus == 0

    again = sign_outcome(today, first.streak, today, 10, (7, 30))
    assert again.already is True
    assert again.awarded == 0

    day7 = today + timedelta(days=6)
    continued = sign_outcome(day7 - timedelta(days=1), 6, day7, 10, (7, 30))
    assert continued.streak == 7
    assert continued.bonus == 20
    assert continued.awarded == 30

    skipped = sign_outcome(today, 4, today + timedelta(days=2), 10, (7,))
    assert skipped.streak == 1
    assert skipped.awarded == 10


def test_message_points_honor_interval_and_daily_cap():
    state = MessagePointState(0, 0, None)
    today = date(2026, 5, 1)
    gained_days = 0
    for _ in range(150):
        state, gained = apply_message_points(state, today, msg_per_point=50, max_per_day=2)
        gained_days += gained
    assert gained_days == 2
    assert state.total_msgs == 150
    assert state.points_today == 2

    tomorrow = today + timedelta(days=1)
    gained_tomorrow = 0
    for _ in range(50):
        state, gained = apply_message_points(state, tomorrow, 50, 2)
        gained_tomorrow += gained
    assert gained_tomorrow == 1
    assert state.points_today == 1
    assert state.points_date == tomorrow


def test_message_points_can_be_disabled():
    state = MessagePointState(49, 0, date(2026, 5, 1))
    updated, gained = apply_message_points(state, date(2026, 5, 1), 0, 100)
    assert gained == 0
    assert updated.total_msgs == 50


def test_fortune_is_stable_across_calls_and_tied_to_sha256():
    day = date(2026, 3, 18)
    assert daily_fortune(42, day) == daily_fortune(42, day)
    digest = hashlib.sha256(b"42:2026-03-18").digest()
    number = int.from_bytes(digest[:8], "big")
    fortune = daily_fortune(42, day)
    assert fortune.lucky_number == number % 100
    assert fortune.name == FORTUNES[number % len(FORTUNES)][0]
    assert daily_fortune(42, day) != daily_fortune(42, date(2026, 3, 19))
