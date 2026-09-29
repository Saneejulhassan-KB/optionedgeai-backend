"""Forming/closed classification, live tick folding, rollover and precedence."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.market_session import IST, candle_open, to_ist
from app.core.timeframes import resolve_timeframe
from app.models.candle import SOURCE_INTRADAY, SOURCE_WEBSOCKET
from app.repositories.candle_repository import CandleRepository
from app.services.candle_reconciler import (
    apply_tick,
    classify_forming,
    finalize_due_candles,
    missing_window,
)
from tests.conftest import make_candle

KEY = "NSE_INDEX|Nifty 50"
SPEC_5M = resolve_timeframe("5m")


def ist(hh, mm, ss=0, day=13) -> datetime:
    return datetime(2026, 8, day, hh, mm, ss, tzinfo=IST)


def _session_candles(until_hh, until_mm, *, source=SOURCE_INTRADAY):
    """Intraday-style rows from 09:15 up to (and including) the given clock."""
    out = []
    cursor = ist(9, 15)
    end = ist(until_hh, until_mm)
    while cursor <= end:
        out.append(make_candle(timestamp=cursor, price=100.0, source=source))
        cursor += timedelta(minutes=5)
    return out


def test_session_loaded_at_1100_marks_only_the_active_candle_forming():
    candles = classify_forming(_session_candles(11, 0), SPEC_5M, now=ist(11, 2))
    by_clock = {to_ist(c.timestamp).strftime("%H:%M"): c.is_closed for c in candles}

    assert by_clock["09:15"] is True
    assert by_clock["10:55"] is True
    assert by_clock["11:00"] is False
    assert sum(1 for c in candles if not c.is_closed) == 1


def test_app_start_before_market_closes_nothing():
    candles = classify_forming(_session_candles(9, 15), SPEC_5M, now=ist(8, 30))
    assert all(not c.is_closed for c in candles)  # no interval has elapsed yet


def test_app_start_at_1400_closes_the_whole_morning():
    candles = classify_forming(_session_candles(14, 0), SPEC_5M, now=ist(14, 1))
    forming = [c for c in candles if not c.is_closed]
    assert len(forming) == 1
    assert to_ist(forming[0].timestamp).strftime("%H:%M") == "14:00"


def test_tick_creates_then_updates_the_forming_candle(db):
    apply_tick(db, instrument_key=KEY, timeframes=["5m"], ltp=100.0, ts=ist(9, 21))
    apply_tick(db, instrument_key=KEY, timeframes=["5m"], ltp=105.0, ts=ist(9, 22))
    apply_tick(db, instrument_key=KEY, timeframes=["5m"], ltp=95.0, ts=ist(9, 23))

    rows = CandleRepository(db).get_range(instrument_key=KEY, timeframe="5m")
    assert len(rows) == 1
    row = rows[0]
    assert to_ist(row.timestamp).strftime("%H:%M") == "09:20"
    assert float(row.open) == 100.0
    assert float(row.high) == 105.0
    assert float(row.low) == 95.0
    assert float(row.close) == 95.0
    assert row.is_closed is False


def test_duplicate_tick_does_not_duplicate_the_candle(db):
    for _ in range(5):
        apply_tick(db, instrument_key=KEY, timeframes=["5m"], ltp=100.0, ts=ist(9, 21))
    rows = CandleRepository(db).get_range(instrument_key=KEY, timeframe="5m")
    assert len(rows) == 1


def test_rollover_closes_the_previous_candle_and_opens_the_next(db):
    apply_tick(db, instrument_key=KEY, timeframes=["5m"], ltp=100.0, ts=ist(9, 21))
    apply_tick(db, instrument_key=KEY, timeframes=["5m"], ltp=110.0, ts=ist(9, 26))

    rows = CandleRepository(db).get_range(instrument_key=KEY, timeframe="5m")
    assert [to_ist(r.timestamp).strftime("%H:%M") for r in rows] == ["09:20", "09:25"]
    assert rows[0].is_closed is True
    assert rows[1].is_closed is False


def test_ticks_update_every_configured_timeframe(db):
    apply_tick(
        db, instrument_key=KEY, timeframes=["3m", "5m", "15m"], ltp=100.0, ts=ist(9, 21)
    )
    repo = CandleRepository(db)
    for timeframe, expected_open in [("3m", "09:21"), ("5m", "09:20"), ("15m", "09:15")]:
        rows = repo.get_range(instrument_key=KEY, timeframe=timeframe)
        assert len(rows) == 1
        assert to_ist(rows[0].timestamp).strftime("%H:%M") == expected_open


def test_forming_candle_is_finalized_by_the_clock_without_any_tick(db):
    apply_tick(db, instrument_key=KEY, timeframes=["5m"], ltp=100.0, ts=ist(9, 21))
    closed = finalize_due_candles(db, instrument_key=KEY, timeframe="5m", now=ist(9, 40))
    assert closed == 1
    rows = CandleRepository(db).get_range(instrument_key=KEY, timeframe="5m")
    assert rows[0].is_closed is True


def test_live_tick_cannot_overwrite_a_finalized_provider_candle(db):
    repo = CandleRepository(db)
    open_ts = candle_open(ist(9, 21), SPEC_5M)
    repo.bulk_upsert(
        [make_candle(timestamp=open_ts, price=200.0, is_closed=True, source=SOURCE_INTRADAY)]
    )

    apply_tick(db, instrument_key=KEY, timeframes=["5m"], ltp=1.0, ts=ist(9, 21))

    row = repo.get_range(instrument_key=KEY, timeframe="5m")[0]
    assert float(row.close) == 200.0
    assert row.source == SOURCE_INTRADAY


def test_provider_candle_overwrites_a_websocket_candle(db):
    repo = CandleRepository(db)
    apply_tick(db, instrument_key=KEY, timeframes=["5m"], ltp=100.0, ts=ist(9, 21))
    open_ts = candle_open(ist(9, 21), SPEC_5M)

    repo.bulk_upsert(
        [make_candle(timestamp=open_ts, price=201.0, is_closed=True, source=SOURCE_INTRADAY)]
    )
    row = repo.get_range(instrument_key=KEY, timeframe="5m")[0]
    assert float(row.close) == 201.0
    assert row.is_closed is True
    assert row.source == SOURCE_INTRADAY


def test_closed_candle_never_reverts_to_forming(db):
    repo = CandleRepository(db)
    open_ts = candle_open(ist(9, 21), SPEC_5M)
    repo.bulk_upsert([make_candle(timestamp=open_ts, price=100.0, is_closed=True)])
    repo.bulk_upsert(
        [make_candle(timestamp=open_ts, price=101.0, is_closed=False, source="upstox_historical")]
    )
    assert repo.get_range(instrument_key=KEY, timeframe="5m")[0].is_closed is True


def test_missing_window_starts_at_the_last_closed_candle(db):
    repo = CandleRepository(db)
    repo.bulk_upsert(
        [
            make_candle(timestamp=ist(10, 10), is_closed=True),
            make_candle(timestamp=ist(10, 15), is_closed=True),
            make_candle(timestamp=ist(10, 20), is_closed=False, source=SOURCE_WEBSOCKET),
        ]
    )
    start, end = missing_window(db, instrument_key=KEY, timeframe="5m", now=ist(10, 37))
    assert to_ist(start).strftime("%H:%M") == "10:20"
    assert to_ist(end).strftime("%H:%M") == "10:37"


def test_missing_window_is_unknown_when_nothing_is_stored(db):
    start, _ = missing_window(db, instrument_key=KEY, timeframe="5m", now=ist(10, 37))
    assert start is None
