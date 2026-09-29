"""
/api/v1/market — historical foundation APIs for Flutter bootstrap.

Does not replace legacy /market/* live proxy routes.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.timeframes import resolve_timeframe
from app.database import get_db
from app.dependencies.auth import get_current_user, get_current_user_optional
from app.dependencies.upstox_token import get_upstox_access_token
from app.models.historical_coverage import STATUS_COMPLETE, STATUS_FAILED
from app.models.user import User
from app.repositories.candle_repository import CandleRepository
from app.schemas.market_v1 import (
    BootstrapResponse,
    MarketStateResponse,
    StoredCandleOut,
    StoredCandlesResponse,
    SyncResponse,
    SyncSeriesResult,
    VerificationResponse,
)
from app.services import sync_jobs
from app.services.candle_normalize import parse_timestamp
from app.services.market_bootstrap import (
    build_bootstrap_payload,
    build_verification_report,
    configured_instruments,
    configured_timeframes,
    run_market_sync,
)
from app.services.market_state import build_market_state
from app.services.upstox_client import UpstoxAPIError
from app.websocket.feed_state import get_feed_health

router = APIRouter(prefix="/api/v1/market", tags=["Market Data v1"])


def _parse_bound(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    try:
        if len(text) == 10:
            d = date.fromisoformat(text)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        return parse_timestamp(text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid timestamp '{raw}'") from exc


def _feed_snapshot(user: User | None) -> dict[str, Any]:
    """Real upstream feed state — never an assumed `connected: true`."""
    settings = get_settings()
    stale_after = float(settings.data_stale_threshold_seconds)
    if user is None:
        return {
            "status": "DISCONNECTED",
            "connected": False,
            "data_fresh": False,
            "staleness_seconds": None,
            "stale_after_seconds": stale_after,
            "reason": "No authenticated user; feed state unknown",
        }
    return get_feed_health(user.id).snapshot(stale_after_seconds=stale_after)


@router.get(
    "/bootstrap",
    response_model=BootstrapResponse,
    summary="Market bootstrap snapshot for Flutter startup",
)
def market_bootstrap(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> BootstrapResponse:
    """
    Coverage + readiness + market state, reported exactly as observed.

    Does not download history by itself — call POST /sync first (or on login).
    """
    payload = build_bootstrap_payload(
        db,
        feed=_feed_snapshot(user),
        user_id=user.id if user else None,
    )
    return BootstrapResponse(**payload)


@router.post(
    "/sync",
    response_model=SyncResponse,
    summary="Download/resume full Upstox history + today's session",
)
async def market_sync(
    user: User = Depends(get_current_user),
    access_token: str = Depends(get_upstox_access_token),
    include_today: bool = Query(default=True),
) -> SyncResponse:
    """
    Resumable full-history sync for configured instruments/timeframes, then
    reconstruct today's session via Upstox intraday V3.

    Uses a dedicated DB session (not request-scoped yield) because the sync
    performs many chunked Upstox calls.
    """
    from app.database.session import SessionLocal

    settings = get_settings()
    if not settings.historical_sync_enabled:
        return SyncResponse(status="DISABLED")

    total = len(configured_instruments()) * len(configured_timeframes())
    job = sync_jobs.start_job(user.id, total_series=total)
    started = time.monotonic()

    db = SessionLocal()
    try:
        summaries = await run_market_sync(
            db,
            access_token,
            include_today=include_today,
            on_series=lambda s: sync_jobs.record_series(
                user.id, {"series": f"{s.instrument_key}|{s.timeframe}", "status": s.status}
            ),
        )
    except UpstoxAPIError as exc:
        sync_jobs.finish_job(user.id, status=sync_jobs.JOB_FAILED, error=exc.message)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        sync_jobs.finish_job(user.id, status=sync_jobs.JOB_FAILED, error=str(exc))
        raise
    finally:
        db.close()

    results = [
        SyncSeriesResult(
            instrument_key=s.instrument_key,
            timeframe=s.timeframe,
            status=s.status,
            reason=s.reason,
            requested_from=s.requested_from.isoformat() if s.requested_from else None,
            requested_to=s.requested_to.isoformat() if s.requested_to else None,
            chunks_planned=s.chunks_planned,
            chunks_skipped=s.chunks_skipped,
            chunks_completed=s.chunks_completed,
            chunks_failed=s.chunks_failed,
            candles_upserted=s.candles_upserted,
            candle_count=s.candle_count,
            suspicious_gap_count=s.suspicious_gap_count,
            earliest=s.earliest.isoformat() if s.earliest else None,
            latest=s.latest.isoformat() if s.latest else None,
            errors=s.errors,
        )
        for s in summaries
    ]

    if not results:
        status = "DISABLED"
    elif all(r.status == STATUS_COMPLETE for r in results):
        status = sync_jobs.JOB_COMPLETE
    elif all(r.status == STATUS_FAILED for r in results):
        status = sync_jobs.JOB_FAILED
    else:
        status = sync_jobs.JOB_PARTIAL

    sync_jobs.finish_job(user.id, status=status)
    return SyncResponse(
        status=status,
        job_id=job.job_id,
        duration_seconds=round(time.monotonic() - started, 2),
        results=results,
    )


@router.get(
    "/sync/status",
    summary="Progress of the most recent synchronization for this user",
)
def market_sync_status(user: User = Depends(get_current_user)) -> dict:
    job = sync_jobs.get_job(user.id)
    return job.as_dict() if job else {"status": "IDLE"}


@router.get(
    "/verify",
    response_model=VerificationResponse,
    summary="Evidence of actual historical coverage per instrument/timeframe",
)
def market_verify(db: Session = Depends(get_db)) -> VerificationResponse:
    return VerificationResponse(
        server_time=datetime.now(timezone.utc).isoformat(),
        series=build_verification_report(db),
    )


@router.get(
    "/candles",
    response_model=StoredCandlesResponse,
    summary="Read normalized candles from local historical store",
)
def market_candles_v1(
    instrument_key: str = Query(..., description="Upstox instrument_key"),
    timeframe: str = Query("5m", description="1m,3m,5m,15m,30m,1h,1D"),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    limit: int | None = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> StoredCandlesResponse:
    try:
        spec = resolve_timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from_ts = _parse_bound(from_)
    to_ts = _parse_bound(to)

    repo = CandleRepository(db)
    if from_ts is None and to_ts is None:
        rows = repo.get_range(
            instrument_key=instrument_key, timeframe=spec.id, limit=limit
        )
    else:
        rows = repo.get_range(
            instrument_key=instrument_key,
            timeframe=spec.id,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]

    return StoredCandlesResponse(
        instrument_key=instrument_key,
        timeframe=spec.id,
        from_ts=from_ts.isoformat() if from_ts else None,
        to_ts=to_ts.isoformat() if to_ts else None,
        count=len(rows),
        candles=[
            StoredCandleOut(
                timestamp=c.timestamp.isoformat(),
                open=float(c.open),
                high=float(c.high),
                low=float(c.low),
                close=float(c.close),
                volume=int(c.volume),
                open_interest=c.open_interest,
                is_closed=c.is_closed,
                source=c.source,
            )
            for c in rows
        ],
    )


@router.get(
    "/state",
    response_model=MarketStateResponse,
    summary="Latest normalized market state for an instrument",
)
def market_state_v1(
    instrument_key: str = Query(...),
    timeframe: str = Query("5m"),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> MarketStateResponse:
    try:
        resolve_timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = build_market_state(
        db,
        instrument_key=instrument_key,
        timeframe=timeframe,
        feed=_feed_snapshot(user),
    )
    return MarketStateResponse(state=state)


@router.get(
    "/coverage",
    summary="List historical coverage metadata",
)
def market_coverage(db: Session = Depends(get_db)) -> dict:
    repo = CandleRepository(db)
    rows = repo.list_coverage()
    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "coverage": [
            {
                "instrument_key": r.instrument_key,
                "timeframe": r.timeframe,
                "status": r.status,
                "candle_count": r.candle_count,
                "requested_from": r.requested_from.isoformat() if r.requested_from else None,
                "requested_to": r.requested_to.isoformat() if r.requested_to else None,
                "earliest": r.earliest_timestamp.isoformat() if r.earliest_timestamp else None,
                "latest": r.latest_timestamp.isoformat() if r.latest_timestamp else None,
                "suspicious_gap_count": r.suspicious_gap_count,
                "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
                "last_successful_sync": (
                    r.last_successful_sync.isoformat() if r.last_successful_sync else None
                ),
                "last_error": r.last_error,
            }
            for r in rows
        ],
    }
