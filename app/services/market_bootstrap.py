"""
Market bootstrap — sync configured instruments/timeframes, then report the
real state of the data foundation (no optimistic values).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.market_session import session_phase
from app.core.timeframes import parse_timeframe_list, resolve_timeframe
from app.models.historical_coverage import (
    STATUS_COMPLETE,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_REQUESTED,
)
from app.repositories.candle_repository import CandleRepository
from app.services.coverage_validator import detect_gaps
from app.services.historical_download import HistoricalDownloadService, SyncSummary
from app.services.market_state import market_state_from_context
from app.services.readiness import (
    build_timeframe_context,
    evaluate_readiness,
    trading_timeframes,
)
from app.services.sync_jobs import get_job

logger = logging.getLogger(__name__)


def configured_instruments() -> list[str]:
    settings = get_settings()
    return [p.strip() for p in settings.historical_instruments.split(",") if p.strip()]


def configured_timeframes() -> list[str]:
    settings = get_settings()
    return [t.id for t in parse_timeframe_list(settings.historical_timeframes)]


async def run_market_sync(
    db: Session,
    access_token: str,
    *,
    include_today: bool = True,
    instruments: list[str] | None = None,
    timeframes: list[str] | None = None,
    on_series: Any = None,
) -> list[SyncSummary]:
    """Full-history (resumable) + optional today session for each configured series."""
    settings = get_settings()
    if not settings.historical_sync_enabled:
        logger.info("historical_sync_skipped reason=disabled")
        return []

    keys = instruments or configured_instruments()
    tfs = timeframes or configured_timeframes()
    service = HistoricalDownloadService(db, access_token)
    summaries: list[SyncSummary] = []

    # Daily candles establish the provider's real trading-day calendar, which
    # gap detection and coverage verification rely on, so sync them first.
    ordered = sorted(tfs, key=lambda t: 0 if t == "1D" else 1)

    for key in keys:
        for tf in ordered:
            summary = await service.sync_full_history(instrument_key=key, timeframe=tf)
            if include_today:
                try:
                    await service.sync_today_session(instrument_key=key, timeframe=tf)
                except Exception as exc:  # noqa: BLE001 — today is optional context
                    logger.warning(
                        "today_sync_failed instrument=%s timeframe=%s err=%s", key, tf, exc
                    )
            summaries.append(summary)
            if on_series is not None:
                on_series(summary)
    return summaries


def build_verification_report(db: Session) -> list[dict[str, Any]]:
    """
    Evidence for every configured series: what was asked for vs. what exists.

    This is what makes a COMPLETE claim checkable.
    """
    repo = CandleRepository(db)
    settings = get_settings()
    out: list[dict[str, Any]] = []

    for key in configured_instruments():
        for tf in configured_timeframes():
            spec = resolve_timeframe(tf)
            cov = repo.get_or_create_coverage(instrument_key=key, timeframe=spec.id)
            chunks = repo.chunk_status_counts(instrument_key=key, timeframe=spec.id)
            report = detect_gaps(
                repo,
                instrument_key=key,
                spec=spec,
                scan_days=int(settings.coverage_gap_scan_days),
            )
            out.append(
                {
                    "instrument_key": key,
                    "timeframe": spec.id,
                    "provider": cov.provider,
                    "requested_from": _iso(cov.requested_from),
                    "requested_to": _iso(cov.requested_to),
                    "actual_earliest": _iso(cov.earliest_timestamp),
                    "actual_latest": _iso(cov.latest_timestamp),
                    "candle_count": cov.candle_count,
                    "chunks_completed": chunks.get("COMPLETED", 0),
                    "chunks_failed": chunks.get("FAILED", 0),
                    "suspicious_gap_count": report.suspicious_gap_count
                    if report.verifiable
                    else cov.suspicious_gap_count,
                    "gap_scan": report.as_dict(),
                    "sync_status": cov.status,
                    "last_successful_sync": _iso(cov.last_successful_sync),
                    "last_error": cov.last_error,
                }
            )
    return out


def build_bootstrap_payload(
    db: Session,
    *,
    feed: dict[str, Any] | None = None,
    user_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble GET /api/v1/market/bootstrap response (no secrets, no optimism)."""
    current = now or datetime.now(timezone.utc)
    keys = configured_instruments()
    all_tfs = configured_timeframes()
    critical_tfs = [tf for tf in trading_timeframes() if tf in all_tfs] or all_tfs
    repo = CandleRepository(db)
    settings = get_settings()
    primary_tf = resolve_timeframe(settings.primary_timeframe).id

    coverage_out: dict[str, Any] = {}
    states: dict[str, Any] = {}
    latest_candles: dict[str, Any] = {}
    readiness_by_instrument: dict[str, Any] = {}

    can_trade_all = bool(keys)
    statuses: list[str] = []

    for key in keys:
        contexts = {
            tf: build_timeframe_context(
                db, instrument_key=key, timeframe=tf, now=current
            )
            for tf in critical_tfs
        }
        readiness = evaluate_readiness(contexts, feed=feed, now=current)
        readiness_by_instrument[key] = readiness.as_dict()
        statuses.append(readiness.status)
        can_trade_all = can_trade_all and readiness.can_trade

        for tf in all_tfs:
            cov = repo.get_or_create_coverage(instrument_key=key, timeframe=tf)
            cov_key = f"{key}|{tf}"
            coverage_out[cov_key] = {
                "instrument_key": key,
                "timeframe": tf,
                "status": cov.status,
                "candle_count": cov.candle_count,
                "requested_from": _iso(cov.requested_from),
                "requested_to": _iso(cov.requested_to),
                "earliest": _iso(cov.earliest_timestamp),
                "latest": _iso(cov.latest_timestamp),
                "suspicious_gap_count": cov.suspicious_gap_count,
                "last_sync_at": _iso(cov.last_sync_at),
                "last_successful_sync": _iso(cov.last_successful_sync),
                "last_error": cov.last_error,
            }

            if tf in contexts:
                states[cov_key] = market_state_from_context(contexts[tf], readiness)

            recent = repo.get_range(instrument_key=key, timeframe=tf, limit=5)
            latest_candles[cov_key] = [
                {
                    "timestamp": c.timestamp.isoformat(),
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": int(c.volume),
                    "is_closed": c.is_closed,
                    "source": c.source,
                }
                for c in recent
            ]

    job = get_job(user_id) if user_id is not None else None
    sync_active = job is not None and job.is_active
    overall = _overall_status(statuses, sync_active=sync_active)

    payload = {
        "status": overall,
        "can_trade": can_trade_all and not sync_active,
        "server_time": current.isoformat(),
        "market_session": session_phase(current).phase,
        "instruments": keys,
        "timeframes": all_tfs,
        "trading_timeframes": critical_tfs,
        "primary_timeframe": primary_tf,
        "websocket": feed or {"status": "DISCONNECTED", "connected": False, "data_fresh": False},
        "sync": job.as_dict() if job else {"status": "IDLE"},
        "readiness": readiness_by_instrument,
        "historical_coverage": coverage_out,
        "latest_candles": latest_candles,
        "market_state": states,
        "data_quality": {
            "overall_status": overall,
            "series_count": len(coverage_out),
            "series_complete": sum(
                1 for c in coverage_out.values() if c["status"] == STATUS_COMPLETE
            ),
            "series_partial": sum(
                1 for c in coverage_out.values() if c["status"] == STATUS_PARTIAL
            ),
            "series_failed": sum(
                1 for c in coverage_out.values() if c["status"] == STATUS_FAILED
            ),
            "series_pending": sum(
                1
                for c in coverage_out.values()
                if c["status"] in {STATUS_REQUESTED, STATUS_DOWNLOADING}
            ),
            "suspicious_gap_total": sum(
                int(c["suspicious_gap_count"] or 0) for c in coverage_out.values()
            ),
        },
    }
    logger.info(
        "market_bootstrap status=%s can_trade=%s ws=%s",
        overall,
        payload["can_trade"],
        (feed or {}).get("status", "DISCONNECTED"),
    )
    return payload


def _overall_status(statuses: list[str], *, sync_active: bool) -> str:
    if sync_active:
        return "HISTORICAL_LOADING"
    if not statuses:
        return "INITIALIZING"
    # Report the least-ready instrument.
    priority = [
        "INITIALIZING",
        "HISTORICAL_LOADING",
        "TODAY_LOADING",
        "CALCULATING",
        "DEGRADED",
        "READY",
    ]
    return min(statuses, key=lambda s: priority.index(s) if s in priority else 0)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
