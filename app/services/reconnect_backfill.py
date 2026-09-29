"""
Post-reconnect repair: re-request only the market data that was missed.

Every configured intraday timeframe is repaired, not just 5m, and the request
window is derived from the last stored closed candle instead of blindly
re-downloading the whole session.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.market_session import ensure_utc, to_ist
from app.core.timeframes import parse_timeframe_list
from app.services.candle_reconciler import finalize_due_candles, missing_window
from app.services.historical_download import HistoricalDownloadService

logger = logging.getLogger(__name__)


def backfill_timeframes() -> list[str]:
    settings = get_settings()
    return [t.id for t in parse_timeframe_list(settings.reconnect_backfill_timeframes)]


async def backfill_after_reconnect(
    db: Session,
    access_token: str,
    *,
    instrument_key: str,
    timeframes: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Repair each configured timeframe for one instrument. Returns upsert counts."""
    current = ensure_utc(now or datetime.now(timezone.utc))
    today_ist = to_ist(current).date()
    tfs = timeframes or backfill_timeframes()
    service = HistoricalDownloadService(db, access_token)
    results: dict[str, int] = {}

    for timeframe in tfs:
        gap_start, _ = missing_window(
            db, instrument_key=instrument_key, timeframe=timeframe, now=current
        )
        logger.info(
            "websocket_backfill_started instrument=%s timeframe=%s from=%s to=%s",
            instrument_key,
            timeframe,
            gap_start.isoformat() if gap_start else "unknown",
            current.isoformat(),
        )

        upserted = 0
        # A disconnect spanning previous sessions needs the historical endpoint;
        # the intraday endpoint only ever returns the current day.
        if gap_start is not None:
            gap_day = to_ist(gap_start).date()
            if gap_day < today_ist:
                upserted += await service.sync_range(
                    instrument_key=instrument_key,
                    timeframe=timeframe,
                    from_date=gap_day,
                    to_date=today_ist - timedelta(days=1),
                )

        try:
            upserted += await service.sync_today_session(
                instrument_key=instrument_key,
                timeframe=timeframe,
                now=current,
            )
        except Exception as exc:  # noqa: BLE001 — one timeframe must not kill the rest
            logger.warning(
                "websocket_backfill_failed instrument=%s timeframe=%s err=%s",
                instrument_key,
                timeframe,
                exc,
            )

        finalize_due_candles(
            db, instrument_key=instrument_key, timeframe=timeframe, now=current
        )
        results[timeframe] = upserted
        logger.info(
            "websocket_backfill_completed instrument=%s timeframe=%s upserted=%s",
            instrument_key,
            timeframe,
            upserted,
        )

    return results
