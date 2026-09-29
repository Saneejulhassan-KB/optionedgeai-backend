"""Indicator warmup: a computable number is not the same as a trustworthy one."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.candle import Candle
from app.services.indicators import READY, UNAVAILABLE, WARMING_UP, compute_indicators


def _bars(n: int) -> list[Candle]:
    start = datetime(2024, 6, 3, 3, 45, tzinfo=timezone.utc)
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        price += 0.1
        out.append(
            Candle(
                instrument_key="NSE_INDEX|Nifty 50",
                timeframe="5m",
                timestamp=start + timedelta(minutes=5 * i),
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=100,
                is_closed=True,
                source="test",
            )
        )
    return out


def test_no_candles_makes_every_indicator_unavailable():
    snap = compute_indicators([])
    assert snap.ema21.status == UNAVAILABLE
    assert snap.rsi9.status == UNAVAILABLE
    assert snap.atr14.status == UNAVAILABLE


def test_short_history_keeps_ema200_without_a_value():
    snap = compute_indicators(_bars(50))
    assert snap.ema21.status == READY
    assert snap.ema200.status == WARMING_UP
    assert snap.ema200.value is None


def test_ema200_with_a_value_but_no_warmup_is_still_warming():
    snap = compute_indicators(_bars(220))
    assert snap.ema200.value is not None
    assert snap.ema200.status == WARMING_UP
    assert snap.ema200.required_bars == 400


def test_ema200_is_ready_once_warmed():
    snap = compute_indicators(_bars(450))
    assert snap.ema200.status == READY


def test_rsi_and_atr_require_more_than_their_minimum():
    just_enough = compute_indicators(_bars(16))
    assert just_enough.rsi9.value is not None
    assert just_enough.rsi9.status == WARMING_UP
    assert just_enough.atr14.status == WARMING_UP

    warmed = compute_indicators(_bars(60))
    assert warmed.rsi9.status == READY
    assert warmed.atr14.status == READY


def test_warmup_multiplier_is_configurable():
    snap = compute_indicators(_bars(220), warmup_multiplier=1.0)
    assert snap.ema200.status == READY


def test_vwap_is_unavailable_without_volume():
    bars = _bars(60)
    for bar in bars:
        bar.volume = 0
    assert compute_indicators(bars).vwap.status == UNAVAILABLE
