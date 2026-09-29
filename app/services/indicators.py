"""
Indicator engine over canonical candles (one source of truth).

EMA / RSI / ATR / VWAP — no separate Upstox calls.

An indicator is READY only when it has enough warmup history to be stable,
not merely when the maths returned a number. Producing a value from the
minimum possible number of bars is treated as WARMING_UP.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.core.market_session import IST
from app.models.candle import Candle

READY = "READY"
WARMING_UP = "WARMING_UP"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class IndicatorPoint:
    name: str
    value: float | None
    status: str  # READY | WARMING_UP | UNAVAILABLE
    required_bars: int  # bars needed before the indicator is trustworthy
    minimum_bars: int  # bars needed before any value exists
    available_bars: int

    @property
    def is_ready(self) -> bool:
        return self.status == READY


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    ema9: IndicatorPoint
    ema21: IndicatorPoint
    ema50: IndicatorPoint
    ema200: IndicatorPoint
    rsi9: IndicatorPoint
    atr14: IndicatorPoint
    vwap: IndicatorPoint

    def as_dict(self) -> dict[str, IndicatorPoint]:
        return {
            "ema9": self.ema9,
            "ema21": self.ema21,
            "ema50": self.ema50,
            "ema200": self.ema200,
            "rsi": self.rsi9,
            "atr": self.atr14,
            "vwap": self.vwap,
        }


def _closes(candles: Sequence[Candle]) -> list[float]:
    return [float(c.close) for c in candles]


def _ema(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _rsi(values: Sequence[float], period: int = 9) -> float | None:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        h = float(candles[i].high)
        low = float(candles[i].low)
        prev_c = float(candles[i - 1].close)
        trs.append(max(h - low, abs(h - prev_c), abs(low - prev_c)))
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _session_vwap(candles: Sequence[Candle], *, as_of: datetime | None = None) -> float | None:
    """VWAP for the IST trading session of `as_of` (defaults to last candle)."""
    if not candles:
        return None
    ref = as_of or candles[-1].timestamp
    if ref.tzinfo is None:
        return None
    session_day = ref.astimezone(IST).date()
    num = Decimal("0")
    den = Decimal("0")
    for c in candles:
        if c.timestamp.astimezone(IST).date() != session_day:
            continue
        typical = (c.high + c.low + c.close) / Decimal(3)
        vol = Decimal(c.volume or 0)
        if vol <= 0:
            continue
        num += typical * vol
        den += vol
    if den <= 0:
        return None
    return float(num / den)


def _point(
    name: str,
    value: float | None,
    *,
    minimum: int,
    required: int,
    available: int,
) -> IndicatorPoint:
    if available <= 0:
        status = UNAVAILABLE
    elif value is None:
        status = WARMING_UP if available < minimum else UNAVAILABLE
    elif available < required:
        status = WARMING_UP
    else:
        status = READY
    return IndicatorPoint(
        name=name,
        value=value,
        status=status,
        required_bars=required,
        minimum_bars=minimum,
        available_bars=available,
    )


def _warmup_bars(period: int, multiplier: float) -> int:
    return max(period, int(math.ceil(period * max(1.0, multiplier))))


def compute_indicators(
    candles: Sequence[Candle],
    *,
    warmup_multiplier: float = 2.0,
) -> IndicatorSnapshot:
    """Compute indicators from oldest→newest candles."""
    n = len(candles)
    closes = _closes(candles)

    def ema_point(period: int) -> IndicatorPoint:
        return _point(
            f"ema{period}",
            _ema(closes, period),
            minimum=period,
            required=_warmup_bars(period, warmup_multiplier),
            available=n,
        )

    return IndicatorSnapshot(
        ema9=ema_point(9),
        ema21=ema_point(21),
        ema50=ema_point(50),
        ema200=ema_point(200),
        rsi9=_point(
            "rsi9",
            _rsi(closes, 9),
            minimum=10,
            required=_warmup_bars(9, max(3.0, warmup_multiplier)),
            available=n,
        ),
        atr14=_point(
            "atr14",
            _atr(candles, 14),
            minimum=15,
            required=_warmup_bars(14, max(3.0, warmup_multiplier)),
            available=n,
        ),
        # Index feeds report no volume, so VWAP is informational, never critical.
        vwap=_point("vwap", _session_vwap(candles), minimum=1, required=1, available=n),
    )
