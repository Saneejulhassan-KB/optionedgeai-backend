"""Market-session boundaries, candle rollover clock and timezone helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.core.market_session import (
    IST,
    candle_end,
    candle_open,
    ensure_utc,
    expected_candle_opens,
    is_candle_closed,
    is_within_session,
    session_bounds_utc,
    session_phase,
    to_ist,
)
from app.core.timeframes import resolve_timeframe


def ist(y, m, d, hh, mm, ss=0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=IST)


def test_intraday_buckets_are_anchored_on_market_open():
    spec = resolve_timeframe("3m")
    assert to_ist(candle_open(ist(2026, 8, 13, 9, 15), spec)).strftime("%H:%M") == "09:15"
    assert to_ist(candle_open(ist(2026, 8, 13, 9, 17, 59), spec)).strftime("%H:%M") == "09:15"
    assert to_ist(candle_open(ist(2026, 8, 13, 9, 18), spec)).strftime("%H:%M") == "09:18"
    assert to_ist(candle_open(ist(2026, 8, 13, 11, 2), spec)).strftime("%H:%M") == "11:00"


def test_five_minute_buckets_follow_the_session_grid():
    spec = resolve_timeframe("5m")
    for clock, expected in [
        ((9, 15), "09:15"),
        ((9, 19), "09:15"),
        ((9, 20), "09:20"),
        ((10, 57), "10:55"),
        ((11, 0), "11:00"),
    ]:
        got = to_ist(candle_open(ist(2026, 8, 13, *clock), spec))
        assert got.strftime("%H:%M") == expected


def test_fifteen_minute_bucket_and_end():
    spec = resolve_timeframe("15m")
    open_ts = candle_open(ist(2026, 8, 13, 10, 7), spec)
    assert to_ist(open_ts).strftime("%H:%M") == "10:00"
    assert to_ist(candle_end(open_ts, spec)).strftime("%H:%M") == "10:15"


def test_last_candle_of_the_day_is_clipped_to_market_close():
    spec = resolve_timeframe("15m")
    open_ts = candle_open(ist(2026, 8, 13, 15, 20), spec)
    assert to_ist(open_ts).strftime("%H:%M") == "15:15"
    assert to_ist(candle_end(open_ts, spec)).strftime("%H:%M") == "15:30"


def test_candle_closed_only_after_its_interval_elapses():
    spec = resolve_timeframe("5m")
    open_ts = candle_open(ist(2026, 8, 13, 9, 20), spec)
    assert not is_candle_closed(open_ts, spec, now=ist(2026, 8, 13, 9, 23))
    assert is_candle_closed(open_ts, spec, now=ist(2026, 8, 13, 9, 25))


def test_expected_candle_opens_cover_the_whole_session():
    spec = resolve_timeframe("5m")
    opens = expected_candle_opens(date(2026, 8, 13), spec)
    assert len(opens) == 75  # 09:15 → 15:30 in 5-minute steps
    assert to_ist(opens[0]).strftime("%H:%M") == "09:15"
    assert to_ist(opens[-1]).strftime("%H:%M") == "15:25"


def test_session_bounds_and_phase():
    open_utc, close_utc = session_bounds_utc(date(2026, 8, 13))
    assert to_ist(open_utc).strftime("%H:%M") == "09:15"
    assert to_ist(close_utc).strftime("%H:%M") == "15:30"

    assert session_phase(ist(2026, 8, 13, 8, 0)).phase == "PRE_OPEN"
    assert session_phase(ist(2026, 8, 13, 11, 0)).phase == "OPEN"
    assert session_phase(ist(2026, 8, 13, 16, 0)).phase == "CLOSED"
    assert session_phase(ist(2026, 8, 15, 11, 0)).phase == "NON_TRADING_DAY"  # Saturday


def test_is_within_session_rejects_weekend_and_after_hours():
    assert is_within_session(ist(2026, 8, 13, 10, 0))
    assert not is_within_session(ist(2026, 8, 13, 16, 0))
    assert not is_within_session(ist(2026, 8, 15, 10, 0))


def test_ensure_utc_treats_naive_as_utc_and_converts_offsets():
    naive = datetime(2026, 8, 13, 5, 30)
    assert ensure_utc(naive).tzinfo is timezone.utc
    assert ensure_utc(naive).hour == 5

    aware = ist(2026, 8, 13, 11, 0)
    assert ensure_utc(aware).strftime("%H:%M") == "05:30"
