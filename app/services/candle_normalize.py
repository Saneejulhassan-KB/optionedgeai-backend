"""
Normalize + validate raw Upstox candle rows into canonical candle dicts.

Identity: instrument_key + timeframe + timestamp (UTC).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
logger = logging.getLogger(__name__)


class CandleValidationError(ValueError):
    """One candle failed validation."""


@dataclass(frozen=True, slots=True)
class NormalizedCandle:
    instrument_key: str
    timeframe: str
    timestamp: datetime  # UTC aware
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    open_interest: int | None
    is_closed: bool
    source: str


def _to_decimal(value: Any, *, field: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CandleValidationError(f"Invalid {field}: {value!r}") from exc
    if not d.is_finite():
        raise CandleValidationError(f"Non-finite {field}: {value!r}")
    return d


def parse_timestamp(value: Any) -> datetime:
    """Parse Upstox timestamp → timezone-aware UTC datetime."""
    text = str(value).strip()
    try:
        if "T" in text or text.endswith("Z") or "+" in text[10:]:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(UTC)
    except ValueError as exc:
        raise CandleValidationError(f"Invalid timestamp: {value!r}") from exc


def normalize_upstox_row(
    row: list[Any] | tuple[Any, ...],
    *,
    instrument_key: str,
    timeframe: str,
    source: str,
    is_closed: bool = True,
) -> NormalizedCandle:
    """
    Upstox candle row: [timestamp, open, high, low, close, volume, oi?]
    """
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        raise CandleValidationError(f"Malformed candle row: {row!r}")

    ts = parse_timestamp(row[0])
    o = _to_decimal(row[1], field="open")
    h = _to_decimal(row[2], field="high")
    low = _to_decimal(row[3], field="low")
    c = _to_decimal(row[4], field="close")
    volume = int(row[5] or 0) if len(row) > 5 else 0
    oi: int | None = None
    if len(row) > 6 and row[6] is not None:
        oi = int(row[6])

    if volume < 0:
        raise CandleValidationError(f"Negative volume: {volume}")
    if oi is not None and oi < 0:
        raise CandleValidationError(f"Negative OI: {oi}")

    # OHLC consistency
    if h < low:
        raise CandleValidationError(f"high < low at {ts.isoformat()}")
    if h < o or h < c:
        raise CandleValidationError(f"high below open/close at {ts.isoformat()}")
    if low > o or low > c:
        raise CandleValidationError(f"low above open/close at {ts.isoformat()}")

    return NormalizedCandle(
        instrument_key=instrument_key,
        timeframe=timeframe,
        timestamp=ts,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=volume,
        open_interest=oi,
        is_closed=is_closed,
        source=source,
    )


def normalize_upstox_payload(
    raw: dict[str, Any],
    *,
    instrument_key: str,
    timeframe: str,
    source: str,
    is_closed: bool = True,
) -> list[NormalizedCandle]:
    """Extract + validate candles from an Upstox V3 JSON body."""
    data = raw.get("data") or {}
    rows = data.get("candles") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []

    out: list[NormalizedCandle] = []
    for row in rows:
        try:
            out.append(
                normalize_upstox_row(
                    row,
                    instrument_key=instrument_key,
                    timeframe=timeframe,
                    source=source,
                    is_closed=is_closed,
                )
            )
        except CandleValidationError as exc:
            logger.warning(
                "candle_validation_rejected instrument=%s timeframe=%s err=%s",
                instrument_key,
                timeframe,
                exc,
            )
    # Deduplicate by timestamp (keep last)
    by_ts: dict[datetime, NormalizedCandle] = {c.timestamp: c for c in out}
    return sorted(by_ts.values(), key=lambda c: c.timestamp)
