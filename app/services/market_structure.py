"""
Market structure descriptors from candles — description only, not predictions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.candle import Candle

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True, slots=True)
class MarketStructure:
    trend: str  # up | down | sideways | unknown
    swing_high: float | None
    swing_low: float | None
    previous_day_high: float | None
    previous_day_low: float | None
    previous_close: float | None
    session_high: float | None
    session_low: float | None
    nearest_support: float | None
    nearest_resistance: float | None
    structure_state: str


def _last_session_day(candles: Sequence[Candle]) -> datetime | None:
    if not candles:
        return None
    return candles[-1].timestamp


def compute_structure(candles: Sequence[Candle], *, daily: Sequence[Candle] = ()) -> MarketStructure:
    if not candles:
        return MarketStructure(
            trend="unknown",
            swing_high=None,
            swing_low=None,
            previous_day_high=None,
            previous_day_low=None,
            previous_close=None,
            session_high=None,
            session_low=None,
            nearest_support=None,
            nearest_resistance=None,
            structure_state="NO_DATA",
        )

    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    price = closes[-1]

    # Simple swing: last 5-bar local extremes
    swing_high = None
    swing_low = None
    if len(candles) >= 5:
        window_h = highs[-5:]
        window_l = lows[-5:]
        swing_high = max(window_h)
        swing_low = min(window_l)

    # Trend from EMA-like slope of closes
    if len(closes) >= 21:
        slope = closes[-1] - closes[-21]
        if slope > 0 and price >= (swing_low or price):
            trend = "up"
        elif slope < 0 and price <= (swing_high or price):
            trend = "down"
        else:
            trend = "sideways"
    else:
        trend = "unknown"

    ref = _last_session_day(candles)
    session_day = ref.astimezone(IST).date() if ref and ref.tzinfo else None
    session_high = None
    session_low = None
    if session_day is not None:
        session_bars = [c for c in candles if c.timestamp.astimezone(IST).date() == session_day]
        if session_bars:
            session_high = max(float(c.high) for c in session_bars)
            session_low = min(float(c.low) for c in session_bars)

    pdh = pdl = prev_close = None
    if len(daily) >= 2:
        prev = daily[-2]
        pdh = float(prev.high)
        pdl = float(prev.low)
        prev_close = float(prev.close)
    elif len(daily) == 1:
        prev_close = float(daily[-1].close)

    support = max([v for v in [swing_low, pdl, session_low] if v is not None and v <= price], default=None)
    resistance = min(
        [v for v in [swing_high, pdh, session_high] if v is not None and v >= price],
        default=None,
    )

    return MarketStructure(
        trend=trend,
        swing_high=swing_high,
        swing_low=swing_low,
        previous_day_high=pdh,
        previous_day_low=pdl,
        previous_close=prev_close,
        session_high=session_high,
        session_low=session_low,
        nearest_support=support,
        nearest_resistance=resistance,
        structure_state=trend.upper() if trend != "unknown" else "WARMING_UP",
    )
