"""can_trade must stay false until every critical requirement is satisfied."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.market_session import IST
from app.models.historical_coverage import STATUS_COMPLETE, STATUS_DOWNLOADING, STATUS_PARTIAL
from app.repositories.candle_repository import CandleRepository
from app.services.readiness import evaluate_instrument_readiness
from tests.conftest import make_series

KEY = "NSE_INDEX|Nifty 50"
TIMEFRAMES = ["5m"]

# A Saturday: no session to reconstruct, so market-open gating is disabled and
# the remaining requirements can be exercised in isolation.
WEEKEND = datetime(2026, 8, 15, 12, 0, tzinfo=IST)

HEALTHY_FEED = {"connected": True, "data_fresh": True, "status": "CONNECTED"}


def _seed(db, *, bars: int, coverage: str = STATUS_COMPLETE, gaps: int = 0) -> None:
    repo = CandleRepository(db)
    repo.bulk_upsert(make_series(bars, end=WEEKEND - timedelta(minutes=5)))
    repo.update_coverage(
        instrument_key=KEY,
        timeframe="5m",
        status=coverage,
        suspicious_gap_count=gaps,
    )


def _evaluate(db, *, feed=None, now=WEEKEND, require_market_open=False):
    report, contexts = evaluate_instrument_readiness(
        db,
        instrument_key=KEY,
        timeframes=TIMEFRAMES,
        feed=feed if feed is not None else HEALTHY_FEED,
        now=now,
        require_market_open=require_market_open,
    )
    return report, contexts


def test_everything_ready_allows_trading(db):
    _seed(db, bars=450)
    report, _ = _evaluate(db)

    assert report.can_trade is True
    assert report.status == "READY"
    assert report.blockers == []
    assert report.flags["ema200_ready"] is True
    assert report.flags["5m_ready"] is True


def test_no_data_at_all_is_initializing(db):
    report, _ = _evaluate(db)
    assert report.can_trade is False
    assert report.status == "INITIALIZING"


def test_incomplete_history_blocks_trading(db):
    _seed(db, bars=450, coverage=STATUS_PARTIAL)
    report, _ = _evaluate(db)

    assert report.can_trade is False
    assert report.flags["historical_coverage_ready"] is False
    assert report.status == "HISTORICAL_LOADING"


def test_sync_still_running_blocks_trading(db):
    _seed(db, bars=450, coverage=STATUS_DOWNLOADING)
    report, _ = _evaluate(db)
    assert report.can_trade is False
    assert report.status == "HISTORICAL_LOADING"


def test_ema200_warming_blocks_trading(db):
    # 300 bars produce an EMA200 number but not a trustworthy one (needs 400).
    _seed(db, bars=300)
    report, contexts = _evaluate(db)

    assert contexts["5m"].indicators.ema200.value is not None
    assert contexts["5m"].indicators.ema200.status == "WARMING_UP"
    assert report.flags["ema200_ready"] is False
    assert report.can_trade is False
    assert report.status == "CALCULATING"


def test_rsi_unavailable_blocks_trading(db):
    _seed(db, bars=0)
    report, contexts = _evaluate(db)

    assert contexts["5m"].indicators.rsi9.status == "UNAVAILABLE"
    assert report.flags["rsi_ready"] is False
    assert report.can_trade is False


def test_structure_needs_enough_history(db):
    _seed(db, bars=20)
    report, _ = _evaluate(db)

    assert report.flags["market_structure_ready"] is False
    assert report.can_trade is False


def test_disconnected_websocket_blocks_trading(db):
    _seed(db, bars=450)
    report, _ = _evaluate(
        db, feed={"connected": False, "data_fresh": False, "status": "DISCONNECTED"}
    )

    assert report.can_trade is False
    assert report.status == "DEGRADED"
    assert any("DISCONNECTED" in b for b in report.blockers)


def test_stale_feed_blocks_trading(db):
    _seed(db, bars=450)
    report, _ = _evaluate(
        db,
        feed={
            "connected": True,
            "data_fresh": False,
            "status": "DEGRADED",
            "staleness_seconds": 900,
        },
    )

    assert report.can_trade is False
    assert report.flags["live_data_fresh"] is False
    assert any("stale" in b.lower() for b in report.blockers)


def test_suspicious_gaps_block_trading(db):
    _seed(db, bars=450, gaps=3)
    report, _ = _evaluate(db)

    assert report.flags["no_critical_data_gaps"] is False
    assert report.can_trade is False


def test_closed_market_blocks_trading_when_gating_is_enabled(db):
    _seed(db, bars=450)
    report, _ = _evaluate(db, require_market_open=True)

    assert report.can_trade is False
    assert report.flags["market_open"] is False


def test_missing_todays_candles_block_trading(db):
    """Mid-session with nothing stored for today: the session is incomplete."""
    _seed(db, bars=450)
    midday = datetime(2026, 8, 13, 11, 0, tzinfo=IST)  # Thursday
    report, contexts = _evaluate(db, now=midday, require_market_open=True)

    assert contexts["5m"].today_ready is False
    assert contexts["5m"].today_missing_candles > 0
    assert report.can_trade is False
    assert report.status == "TODAY_LOADING"


def test_multi_timeframe_readiness_requires_every_timeframe(db):
    repo = CandleRepository(db)
    for timeframe, step in [("3m", 3), ("5m", 5), ("15m", 15)]:
        bars = 450 if timeframe != "15m" else 100
        repo.bulk_upsert(
            make_series(
                bars,
                end=WEEKEND - timedelta(minutes=step),
                step_minutes=step,
                timeframe=timeframe,
            )
        )
        repo.update_coverage(
            instrument_key=KEY, timeframe=timeframe, status=STATUS_COMPLETE
        )

    report, _ = evaluate_instrument_readiness(
        db,
        instrument_key=KEY,
        timeframes=["3m", "5m", "15m"],
        feed=HEALTHY_FEED,
        now=WEEKEND,
        require_market_open=False,
    )

    assert report.flags["3m_ready"] is True
    assert report.flags["5m_ready"] is True
    assert report.flags["15m_ready"] is False
    assert report.can_trade is False


@pytest.mark.parametrize("bars,expected", [(0, False), (100, False), (450, True)])
def test_readiness_scales_with_available_history(db, bars, expected):
    _seed(db, bars=bars)
    report, _ = _evaluate(db)
    assert report.can_trade is expected
