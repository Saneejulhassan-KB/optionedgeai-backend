"""
Canonical candle reconciliation — the single place where every source merges.

    Historical API ─┐
    Intraday API  ─┼─▶ reconciler ─▶ candles table (forming / closed)
    WebSocket     ─┘

Identity is always instrument_key + timeframe + timestamp (UTC). Source
precedence and the closed-candle rule live in `CandleRepository.bulk_upsert`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.market_session import candle_end, candle_open, ensure_utc, is_candle_closed
from app.core.timeframes import TimeframeSpec, resolve_timeframe
from app.models.candle import SOURCE_WEBSOCKET
from app.repositories.candle_repository import CandleRepository
from app.services.candle_normalize import NormalizedCandle

logger = logging.getLogger(__name__)


def classify_forming(
    candles: list[NormalizedCandle],
    spec: TimeframeSpec,
    *,
    now: datetime | None = None,
) -> list[NormalizedCandle]:
    """
    Mark intraday candles closed/forming from the IST clock.

    Every candle whose interval has already elapsed is CLOSED; only the
    interval containing `now` stays FORMING. Fixes the previous behaviour
    where a session loaded at 11:00 marked all of 09:15–11:00 as forming.
    """
    current = ensure_utc(now or datetime.now(timezone.utc))
    out: list[NormalizedCandle] = []
    for candle in candles:
        closed = is_candle_closed(candle.timestamp, spec, now=current)
        if closed == candle.is_closed:
            out.append(candle)
            continue
        out.append(
            NormalizedCandle(
                instrument_key=candle.instrument_key,
                timeframe=candle.timeframe,
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                open_interest=candle.open_interest,
                is_closed=closed,
                source=candle.source,
            )
        )
    return out


def finalize_due_candles(
    db: Session,
    *,
    instrument_key: str,
    timeframe: str,
    now: datetime | None = None,
) -> int:
    """
    Close any forming candle whose interval has elapsed.

    Called on read paths too, so a candle can never stay open forever just
    because no tick arrived after the boundary.
    """
    spec = resolve_timeframe(timeframe)
    current = ensure_utc(now or datetime.now(timezone.utc))
    repo = CandleRepository(db)

    open_of_current = candle_open(current, spec)
    closed = repo.close_candles_before(
        instrument_key=instrument_key,
        timeframe=spec.id,
        before_ts=open_of_current,
    )
    if closed:
        logger.info(
            "candle_closed instrument=%s timeframe=%s count=%s",
            instrument_key,
            spec.id,
            closed,
        )
    return closed


def apply_tick(
    db: Session,
    *,
    instrument_key: str,
    timeframes: list[str],
    ltp: float,
    ts: datetime | None = None,
) -> list[NormalizedCandle]:
    """
    Fold one live tick into the forming candle of each timeframe.

    Volume/OI are intentionally left to the provider candle APIs: the feed
    reports cumulative day volume, which is not a per-candle value.
    """
    current = ensure_utc(ts or datetime.now(timezone.utc))
    if ltp <= 0:
        return []

    repo = CandleRepository(db)
    price = Decimal(str(ltp))
    written: list[NormalizedCandle] = []

    for timeframe in timeframes:
        try:
            spec = resolve_timeframe(timeframe)
        except ValueError:
            continue

        open_ts = candle_open(current, spec)

        # Rollover: anything older than the active bucket is finished.
        repo.close_candles_before(
            instrument_key=instrument_key,
            timeframe=spec.id,
            before_ts=open_ts,
        )

        existing = repo.get_range(
            instrument_key=instrument_key,
            timeframe=spec.id,
            from_ts=open_ts,
            to_ts=open_ts,
        )
        current_row = existing[0] if existing else None

        if current_row is not None and current_row.is_closed:
            # Provider already finalized this bucket; live data must not touch it.
            continue

        if current_row is None:
            candle = NormalizedCandle(
                instrument_key=instrument_key,
                timeframe=spec.id,
                timestamp=open_ts,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=0,
                open_interest=None,
                is_closed=False,
                source=SOURCE_WEBSOCKET,
            )
            logger.debug(
                "candle_forming instrument=%s timeframe=%s open=%s",
                instrument_key,
                spec.id,
                open_ts.isoformat(),
            )
        else:
            candle = NormalizedCandle(
                instrument_key=instrument_key,
                timeframe=spec.id,
                timestamp=open_ts,
                open=current_row.open,
                high=max(current_row.high, price),
                low=min(current_row.low, price),
                close=price,
                volume=int(current_row.volume or 0),
                open_interest=current_row.open_interest,
                is_closed=False,
                source=SOURCE_WEBSOCKET,
            )

        repo.bulk_upsert([candle])
        written.append(candle)

    return written


def missing_window(
    db: Session,
    *,
    instrument_key: str,
    timeframe: str,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime]:
    """
    Window that must be re-requested after a disconnect.

    Returns (last_known_closed_candle_end, now). `None` means nothing is
    stored yet for this series.
    """
    spec = resolve_timeframe(timeframe)
    current = ensure_utc(now or datetime.now(timezone.utc))
    repo = CandleRepository(db)
    latest = repo.get_latest(
        instrument_key=instrument_key, timeframe=spec.id, closed_only=True
    )
    if latest is None:
        return None, current
    return candle_end(latest.timestamp, spec), current
