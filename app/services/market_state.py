"""
MarketState assembly — indicators + structure + data quality + readiness.

Readiness itself lives in `app.services.readiness`; this module only shapes the
Flutter-facing payload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.market_session import ensure_utc
from app.core.timeframes import resolve_timeframe
from app.services.indicators import IndicatorPoint
from app.services.readiness import (
    ReadinessReport,
    TimeframeContext,
    build_timeframe_context,
    evaluate_readiness,
    trading_timeframes,
)


def _ind(point: IndicatorPoint) -> dict[str, Any]:
    return {
        "value": point.value,
        "status": point.status,
        "required_bars": point.required_bars,
        "minimum_bars": point.minimum_bars,
        "available_bars": point.available_bars,
    }


def market_state_from_context(
    ctx: TimeframeContext,
    readiness: ReadinessReport,
) -> dict[str, Any]:
    latest = ctx.candles[-1] if ctx.candles else None
    structure = ctx.structure
    points = ctx.indicators.as_dict()

    return {
        "instrument_key": ctx.instrument_key,
        "timeframe": ctx.timeframe,
        "timestamp": ensure_utc(latest.timestamp).isoformat() if latest else None,
        "current_price": float(latest.close) if latest else None,
        "current_candle_is_closed": bool(latest.is_closed) if latest else None,
        "trend": structure.trend,
        "structure_state": structure.structure_state,
        "ema9": _ind(points["ema9"]),
        "ema21": _ind(points["ema21"]),
        "ema50": _ind(points["ema50"]),
        "ema200": _ind(points["ema200"]),
        "rsi": _ind(points["rsi"]),
        "atr": _ind(points["atr"]),
        "vwap": _ind(points["vwap"]),
        "previous_day_high": structure.previous_day_high,
        "previous_day_low": structure.previous_day_low,
        "previous_close": structure.previous_close,
        "session_high": structure.session_high,
        "session_low": structure.session_low,
        "nearest_support": structure.nearest_support,
        "nearest_resistance": structure.nearest_resistance,
        "readiness": readiness.as_dict(),
        "data_quality": {
            "candle_count_active": ctx.active_bars,
            "candle_count_stored": ctx.stored_candle_count,
            "coverage_status": ctx.coverage_status,
            "suspicious_gap_count": ctx.suspicious_gap_count,
            "today_ready": ctx.today_ready,
            "today_missing_candles": ctx.today_missing_candles,
            "data_age_seconds": ctx.data_age_seconds,
        },
    }


def build_market_state(
    db: Session,
    *,
    instrument_key: str,
    timeframe: str = "5m",
    feed: dict[str, Any] | None = None,
    now: datetime | None = None,
    require_market_open: bool = True,
) -> dict[str, Any]:
    """Assemble the MarketState for one instrument/timeframe."""
    spec = resolve_timeframe(timeframe)
    current = ensure_utc(now or datetime.now(timezone.utc))

    tfs = trading_timeframes()
    if spec.id not in tfs:
        tfs = [*tfs, spec.id]

    contexts = {
        tf: build_timeframe_context(
            db, instrument_key=instrument_key, timeframe=tf, now=current
        )
        for tf in tfs
    }
    readiness = evaluate_readiness(
        contexts, feed=feed, now=current, require_market_open=require_market_open
    )
    return market_state_from_context(contexts[spec.id], readiness)
