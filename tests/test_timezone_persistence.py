"""Timestamps must round-trip through SQLite as timezone-aware UTC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.market_session import IST, ensure_utc
from app.repositories.candle_repository import CandleRepository
from app.services.candle_normalize import parse_timestamp
from tests.conftest import make_candle

KEY = "NSE_INDEX|Nifty 50"


def test_naive_utc_and_offset_timestamps_all_persist_as_utc(db):
    repo = CandleRepository(db)
    naive = datetime(2026, 8, 13, 4, 0)
    utc = datetime(2026, 8, 13, 4, 5, tzinfo=timezone.utc)
    ist = datetime(2026, 8, 13, 9, 40, tzinfo=IST)  # 04:10 UTC

    repo.bulk_upsert(
        [
            make_candle(timestamp=naive, price=100),
            make_candle(timestamp=utc, price=101),
            make_candle(timestamp=ist, price=102),
        ]
    )

    rows = repo.get_range(instrument_key=KEY, timeframe="5m")
    assert len(rows) == 3
    for row in rows:
        assert row.timestamp.tzinfo is not None
        assert row.timestamp.utcoffset() == timedelta(0)

    assert [r.timestamp.strftime("%H:%M") for r in rows] == ["04:00", "04:05", "04:10"]


def test_iso_z_and_offset_strings_parse_to_the_same_instant():
    assert parse_timestamp("2026-08-13T04:00:00Z") == datetime(
        2026, 8, 13, 4, 0, tzinfo=timezone.utc
    )
    assert parse_timestamp("2026-08-13T09:30:00+05:30") == datetime(
        2026, 8, 13, 4, 0, tzinfo=timezone.utc
    )


def test_provider_naive_timestamp_is_read_as_ist():
    # Upstox sends exchange-local times without an offset.
    assert parse_timestamp("2026-08-13 09:30:00") == datetime(
        2026, 8, 13, 4, 0, tzinfo=timezone.utc
    )


def test_identity_is_the_instant_not_the_wall_clock(db):
    repo = CandleRepository(db)
    utc = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
    same_moment_in_ist = datetime(2026, 8, 13, 9, 30, tzinfo=IST)

    repo.bulk_upsert([make_candle(timestamp=utc, price=100)])
    repo.bulk_upsert([make_candle(timestamp=same_moment_in_ist, price=105)])

    rows = repo.get_range(instrument_key=KEY, timeframe="5m")
    assert len(rows) == 1
    assert float(rows[0].close) == 105.0
    assert ensure_utc(rows[0].timestamp) == utc
