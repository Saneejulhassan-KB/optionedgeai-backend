"""Historical sync must only claim COMPLETE when the data proves it."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.core.market_session import IST
from app.models.historical_coverage import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PARTIAL,
)
from app.repositories.candle_repository import CandleRepository
from app.services.historical_download import HistoricalDownloadService
from app.services.upstox_client import UpstoxAPIError

KEY = "NSE_INDEX|Nifty 50"
TODAY = datetime.now(IST).date()


class FakeUpstox:
    """Records requested windows and replays scripted responses."""

    def __init__(self, *, rows_per_chunk=None, fail_windows=(), empty=False):
        self.requests: list[tuple[date, date]] = []
        self.intraday_calls = 0
        self._rows_per_chunk = rows_per_chunk
        self._fail_windows = set(fail_windows)
        self._empty = empty

    async def get_historical_candles_v3(self, *, instrument_key, unit, interval, to_date, from_date):
        from_d = date.fromisoformat(from_date)
        to_d = date.fromisoformat(to_date)
        self.requests.append((from_d, to_d))
        if (from_d, to_d) in self._fail_windows:
            raise UpstoxAPIError("Upstox rejected the window", status_code=500)
        if self._empty:
            return {"data": {"candles": []}}
        return {"data": {"candles": self._rows(from_d)}}

    async def get_intraday_candles_v3(self, *, instrument_key, unit, interval):
        self.intraday_calls += 1
        return {"data": {"candles": []}}

    def _rows(self, from_d: date):
        count = self._rows_per_chunk or 3
        base = datetime(from_d.year, from_d.month, from_d.day, 9, 15, tzinfo=IST)
        return [
            [
                (base + timedelta(minutes=5 * i)).isoformat(),
                100.0,
                101.0,
                99.0,
                100.5,
                1000,
                0,
            ]
            for i in range(count)
        ]


def _service(db, fake) -> HistoricalDownloadService:
    service = HistoricalDownloadService(db, "test-token")
    service.client = fake
    return service


@pytest.mark.asyncio
async def test_empty_provider_response_cannot_report_complete(db):
    fake = FakeUpstox(empty=True)
    summary = await _service(db, fake).sync_full_history(
        instrument_key=KEY, timeframe="5m", force_from=TODAY - timedelta(days=5)
    )

    assert summary.status == STATUS_FAILED
    assert summary.candle_count == 0
    assert "no candles" in summary.reason.lower()

    cov = CandleRepository(db).get_or_create_coverage(instrument_key=KEY, timeframe="5m")
    assert cov.status == STATUS_FAILED
    assert cov.last_successful_sync is None


@pytest.mark.asyncio
async def test_all_chunks_succeeding_reports_complete(db):
    fake = FakeUpstox()
    summary = await _service(db, fake).sync_full_history(
        instrument_key=KEY, timeframe="5m", force_from=TODAY - timedelta(days=5)
    )

    assert summary.status == STATUS_COMPLETE
    assert summary.chunks_failed == 0
    assert summary.candle_count > 0

    cov = CandleRepository(db).get_or_create_coverage(instrument_key=KEY, timeframe="5m")
    assert cov.status == STATUS_COMPLETE
    assert cov.last_successful_sync is not None
    assert cov.requested_from is not None


@pytest.mark.asyncio
async def test_failed_chunk_leaves_the_series_partial(db):
    start = TODAY - timedelta(days=60)
    fake = FakeUpstox()
    service = _service(db, fake)

    # Discover the planned windows, then make the second one fail.
    await service.sync_full_history(instrument_key=KEY, timeframe="5m", force_from=start)
    planned = list(fake.requests)
    assert len(planned) >= 2

    failing = FakeUpstox(fail_windows={planned[1]})
    service2 = _service(db, failing)
    summary = await service2.sync_full_history(
        instrument_key=KEY, timeframe="5m", force_from=start, force_redownload=True
    )

    assert summary.chunks_failed == 1
    assert summary.status == STATUS_PARTIAL
    assert "chunk" in summary.reason
    # The other chunks were still downloaded rather than aborting the run.
    assert summary.chunks_completed == len(planned) - 1


@pytest.mark.asyncio
async def test_completed_chunks_are_skipped_on_resume(db):
    start = TODAY - timedelta(days=60)
    first = FakeUpstox()
    await _service(db, first).sync_full_history(
        instrument_key=KEY, timeframe="5m", force_from=start
    )
    planned = len(first.requests)
    assert planned >= 2

    second = FakeUpstox()
    summary = await _service(db, second).sync_full_history(
        instrument_key=KEY, timeframe="5m", force_from=start
    )

    assert second.requests == []  # nothing re-downloaded
    assert summary.chunks_skipped == planned
    assert summary.status == STATUS_COMPLETE


@pytest.mark.asyncio
async def test_interrupted_sync_retries_only_the_failed_window(db):
    start = TODAY - timedelta(days=60)
    probe = FakeUpstox()
    await _service(db, probe).sync_full_history(
        instrument_key=KEY, timeframe="5m", force_from=start
    )
    planned = list(probe.requests)

    failing = FakeUpstox(fail_windows={planned[0]})
    await _service(db, failing).sync_full_history(
        instrument_key=KEY, timeframe="5m", force_from=start, force_redownload=True
    )

    retry = FakeUpstox()
    summary = await _service(db, retry).sync_full_history(
        instrument_key=KEY, timeframe="5m", force_from=start
    )

    assert retry.requests == [planned[0]]
    assert summary.chunks_skipped == len(planned) - 1
    assert summary.status == STATUS_COMPLETE


@pytest.mark.asyncio
async def test_overlapping_windows_do_not_duplicate_candles(db):
    start = TODAY - timedelta(days=5)
    service = _service(db, FakeUpstox())
    await service.sync_full_history(instrument_key=KEY, timeframe="5m", force_from=start)
    before = CandleRepository(db).count(instrument_key=KEY, timeframe="5m")

    await _service(db, FakeUpstox()).sync_range(
        instrument_key=KEY, timeframe="5m", from_date=start, to_date=TODAY - timedelta(days=1)
    )

    assert CandleRepository(db).count(instrument_key=KEY, timeframe="5m") == before


@pytest.mark.asyncio
async def test_auth_failure_is_not_retried(db):
    class AuthFail(FakeUpstox):
        async def get_historical_candles_v3(self, **kwargs):
            self.requests.append(
                (date.fromisoformat(kwargs["from_date"]), date.fromisoformat(kwargs["to_date"]))
            )
            raise UpstoxAPIError("token expired", status_code=401)

    fake = AuthFail()
    summary = await _service(db, fake).sync_full_history(
        instrument_key=KEY, timeframe="5m", force_from=TODAY - timedelta(days=5)
    )

    assert len(fake.requests) == 1
    assert summary.status == STATUS_FAILED
