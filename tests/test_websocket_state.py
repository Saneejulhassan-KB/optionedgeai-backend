"""Feed health must reflect reality, and reconnect must repair every timeframe."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.market_session import IST, to_ist
from app.repositories.candle_repository import CandleRepository
from app.services.reconnect_backfill import backfill_after_reconnect, backfill_timeframes
from app.websocket.feed_state import CONNECTED, DEGRADED, DISCONNECTED, FeedHealth
from tests.conftest import make_candle

KEY = "NSE_INDEX|Nifty 50"


def test_a_fresh_feed_is_disconnected_not_connected():
    snap = FeedHealth().snapshot(stale_after_seconds=120)
    assert snap["status"] == DISCONNECTED
    assert snap["connected"] is False
    assert snap["data_fresh"] is False


def test_connect_disconnect_reconnect_transitions():
    health = FeedHealth()

    health.mark_connecting(reconnect=False)
    assert health.status == "CONNECTING"

    health.mark_connected(reconnect=False)
    assert health.status == CONNECTED
    assert health.reconnect_count == 0

    health.mark_disconnected("stream closed")
    assert health.status == DISCONNECTED
    assert health.disconnect_count == 1

    health.mark_connecting(reconnect=True)
    assert health.status == "RECONNECTING"

    health.mark_connected(reconnect=True)
    assert health.reconnect_count == 1
    assert health.last_successful_reconnect is not None


def test_connected_but_silent_feed_reports_degraded():
    health = FeedHealth()
    health.mark_connected(reconnect=False)
    health.mark_message()
    health.last_message_at = datetime.now(timezone.utc) - timedelta(seconds=600)

    snap = health.snapshot(stale_after_seconds=120)
    assert snap["status"] == DEGRADED
    assert snap["connected"] is True
    assert snap["data_fresh"] is False
    assert snap["staleness_seconds"] > 120


def test_recent_message_is_fresh():
    health = FeedHealth()
    health.mark_connected(reconnect=False)
    health.mark_message()

    snap = health.snapshot(stale_after_seconds=120)
    assert snap["status"] == CONNECTED
    assert snap["data_fresh"] is True


def test_backfill_timeframes_come_from_configuration():
    assert backfill_timeframes() == ["3m", "5m", "15m"]
    assert "5m" in backfill_timeframes()


class RecordingService:
    """Stands in for HistoricalDownloadService during reconnect repair."""

    instances: list["RecordingService"] = []

    def __init__(self, db, token):
        self.db = db
        self.today_calls: list[str] = []
        self.range_calls: list[tuple[str, object, object]] = []
        RecordingService.instances.append(self)

    async def sync_today_session(self, *, instrument_key, timeframe, now=None):
        self.today_calls.append(timeframe)
        return 1

    async def sync_range(self, *, instrument_key, timeframe, from_date, to_date):
        self.range_calls.append((timeframe, from_date, to_date))
        return 1


@pytest.fixture()
def recorder(monkeypatch):
    RecordingService.instances.clear()
    monkeypatch.setattr(
        "app.services.reconnect_backfill.HistoricalDownloadService", RecordingService
    )
    return RecordingService


@pytest.mark.asyncio
async def test_reconnect_backfills_every_configured_timeframe(db, recorder):
    now = datetime(2026, 8, 13, 10, 37, tzinfo=IST)
    results = await backfill_after_reconnect(db, "token", instrument_key=KEY, now=now)

    service = recorder.instances[0]
    assert service.today_calls == ["3m", "5m", "15m"]
    assert set(results) == {"3m", "5m", "15m"}


@pytest.mark.asyncio
async def test_reconnect_requests_only_the_missing_days(db, recorder):
    repo = CandleRepository(db)
    repo.bulk_upsert(
        [
            make_candle(
                timeframe="5m",
                timestamp=datetime(2026, 8, 11, 15, 25, tzinfo=IST),
                is_closed=True,
            )
        ]
    )

    now = datetime(2026, 8, 13, 10, 37, tzinfo=IST)
    await backfill_after_reconnect(db, "token", instrument_key=KEY, timeframes=["5m"], now=now)

    service = recorder.instances[0]
    assert len(service.range_calls) == 1
    timeframe, from_date, to_date = service.range_calls[0]
    assert timeframe == "5m"
    assert from_date.isoformat() == "2026-08-11"
    assert to_date.isoformat() == "2026-08-12"


@pytest.mark.asyncio
async def test_same_day_disconnect_only_refreshes_today(db, recorder):
    repo = CandleRepository(db)
    repo.bulk_upsert(
        [
            make_candle(
                timeframe="5m",
                timestamp=datetime(2026, 8, 13, 10, 15, tzinfo=IST),
                is_closed=True,
            )
        ]
    )

    now = datetime(2026, 8, 13, 10, 37, tzinfo=IST)
    await backfill_after_reconnect(db, "token", instrument_key=KEY, timeframes=["5m"], now=now)

    service = recorder.instances[0]
    assert service.range_calls == []
    assert service.today_calls == ["5m"]


@pytest.mark.asyncio
async def test_backfill_finalizes_candles_left_forming(db, recorder):
    repo = CandleRepository(db)
    repo.bulk_upsert(
        [
            make_candle(
                timeframe="5m",
                timestamp=datetime(2026, 8, 13, 10, 15, tzinfo=IST),
                is_closed=False,
                source="websocket",
            )
        ]
    )

    now = datetime(2026, 8, 13, 10, 37, tzinfo=IST)
    await backfill_after_reconnect(db, "token", instrument_key=KEY, timeframes=["5m"], now=now)

    row = repo.get_range(instrument_key=KEY, timeframe="5m")[0]
    assert to_ist(row.timestamp).strftime("%H:%M") == "10:15"
    assert row.is_closed is True
