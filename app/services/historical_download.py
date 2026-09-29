"""
Resumable full-history download from Upstox V3 → persistent candle store.

FULL HISTORY = provider_available_from → yesterday (IST), chunked per Upstox
documented retrieval limits. Today's session is loaded separately through the
intraday endpoint and classified closed/forming by the IST clock.

A sync is only reported COMPLETE when every planned chunk succeeded, candles
were actually stored, and the earliest stored candle really reaches the
requested provider start date.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.market_session import IST, ensure_utc, session_bounds_utc, to_ist
from app.core.timeframes import TimeframeSpec, resolve_timeframe
from app.models.historical_chunk import CHUNK_COMPLETED, CHUNK_FAILED
from app.models.historical_coverage import (
    STATUS_COMPLETE,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_PARTIAL,
)
from app.repositories.candle_repository import CandleRepository
from app.services.candle_normalize import normalize_upstox_payload
from app.services.candle_reconciler import classify_forming
from app.services.coverage_validator import detect_gaps, verify_earliest_reached
from app.services.date_range_planner import plan_chunks, provider_available_from
from app.services.upstox_client import UpstoxAPIError, UpstoxClient

logger = logging.getLogger(__name__)


@dataclass
class SyncSummary:
    instrument_key: str
    timeframe: str
    requested_from: date | None = None
    requested_to: date | None = None
    chunks_planned: int = 0
    chunks_skipped: int = 0
    chunks_completed: int = 0
    chunks_failed: int = 0
    candles_upserted: int = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    candle_count: int = 0
    suspicious_gap_count: int = 0
    status: str = STATUS_DOWNLOADING
    reason: str = ""
    errors: list[str] = field(default_factory=list)

    # Backwards-compatible alias for the older field name.
    @property
    def chunks_requested(self) -> int:
        return self.chunks_planned


class HistoricalDownloadService:
    """Download + persist maximum available Upstox history for one series."""

    def __init__(self, db: Session, access_token: str) -> None:
        self.db = db
        self.repo = CandleRepository(db)
        self.client = UpstoxClient(access_token)
        self.settings = get_settings()

    async def sync_full_history(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        force_from: date | None = None,
        force_redownload: bool = False,
    ) -> SyncSummary:
        spec = resolve_timeframe(timeframe)
        summary = SyncSummary(instrument_key=instrument_key, timeframe=spec.id)

        today = datetime.now(IST).date()
        hist_to = today - timedelta(days=1)
        available_from = force_from or provider_available_from(spec)
        summary.requested_from = available_from
        summary.requested_to = hist_to

        if force_redownload:
            self.repo.clear_chunks(instrument_key=instrument_key, timeframe=spec.id)

        self.repo.update_coverage(
            instrument_key=instrument_key,
            timeframe=spec.id,
            status=STATUS_DOWNLOADING,
            requested_from=_as_utc_day_start(available_from),
            requested_to=_as_utc_day_start(hist_to),
            last_error="",
        )

        logger.info(
            "historical_sync_started instrument=%s timeframe=%s from=%s to=%s",
            instrument_key,
            spec.id,
            available_from.isoformat(),
            hist_to.isoformat(),
        )

        if hist_to < available_from:
            return self._finalize(summary, spec, instrument_key, available_from)

        # Chunk windows are anchored on the provider start date, so the same
        # window key is produced on every run and completed work can be skipped.
        chunks = plan_chunks(timeframe=spec, from_date=available_from, to_date=hist_to)
        summary.chunks_planned = len(chunks)
        already_done = self.repo.completed_chunks(
            instrument_key=instrument_key, timeframe=spec.id
        )

        max_retries = max(1, int(self.settings.historical_max_retries))
        for chunk in chunks:
            key = (chunk.from_date, chunk.to_date)
            if key in already_done:
                summary.chunks_skipped += 1
                continue

            ok, count, error = await self._fetch_chunk_with_retries(
                instrument_key=instrument_key,
                spec=spec,
                from_d=chunk.from_date,
                to_d=chunk.to_date,
                max_retries=max_retries,
            )
            if ok:
                summary.chunks_completed += 1
                summary.candles_upserted += count
                self.repo.record_chunk(
                    instrument_key=instrument_key,
                    timeframe=spec.id,
                    from_date=chunk.from_date,
                    to_date=chunk.to_date,
                    status=CHUNK_COMPLETED,
                    candle_count=count,
                )
            else:
                summary.chunks_failed += 1
                summary.errors.append(error or "chunk failed")
                self.repo.record_chunk(
                    instrument_key=instrument_key,
                    timeframe=spec.id,
                    from_date=chunk.from_date,
                    to_date=chunk.to_date,
                    status=CHUNK_FAILED,
                    last_error=error,
                )
                # A failed chunk leaves a hole; keep going so the rest of the
                # range is still downloaded and the result stays PARTIAL.

        return self._finalize(summary, spec, instrument_key, available_from)

    def _finalize(
        self,
        summary: SyncSummary,
        spec: TimeframeSpec,
        instrument_key: str,
        requested_from: date,
    ) -> SyncSummary:
        """Verify what was actually stored before declaring a status."""
        earliest = self.repo.get_earliest(
            instrument_key=instrument_key, timeframe=spec.id
        )
        latest = self.repo.get_latest(instrument_key=instrument_key, timeframe=spec.id)
        count = self.repo.count(instrument_key=instrument_key, timeframe=spec.id)

        summary.earliest = earliest.timestamp if earliest else None
        summary.latest = latest.timestamp if latest else None
        summary.candle_count = count

        # The historical pass owns everything up to yesterday's close; today's
        # still-incomplete session must not count against it.
        _, yesterday_close = session_bounds_utc(datetime.now(IST).date() - timedelta(days=1))
        report = detect_gaps(
            self.repo,
            instrument_key=instrument_key,
            spec=spec,
            scan_days=int(self.settings.coverage_gap_scan_days),
            scan_to=yesterday_close,
        )
        summary.suspicious_gap_count = report.suspicious_gap_count

        reached, why = verify_earliest_reached(
            self.repo,
            instrument_key=instrument_key,
            timeframe=spec.id,
            requested_from=requested_from,
            actual_earliest=summary.earliest,
        )

        if count == 0:
            summary.status = STATUS_FAILED
            summary.reason = "Provider returned no candles for the requested range"
        elif summary.chunks_failed > 0:
            summary.status = STATUS_PARTIAL
            summary.reason = f"{summary.chunks_failed} chunk(s) failed"
        elif not reached:
            summary.status = STATUS_PARTIAL
            summary.reason = why
        elif report.suspicious_gap_count > 0:
            summary.status = STATUS_PARTIAL
            summary.reason = (
                f"{report.suspicious_gap_count} suspicious gap(s) inside trading sessions"
            )
        else:
            summary.status = STATUS_COMPLETE
            summary.reason = ""

        self.repo.update_coverage(
            instrument_key=instrument_key,
            timeframe=spec.id,
            status=summary.status,
            last_error=summary.reason or (summary.errors[-1] if summary.errors else ""),
            suspicious_gap_count=summary.suspicious_gap_count,
            missing_ranges=[g.as_dict() for g in report.suspicious_gaps],
            mark_success=summary.status == STATUS_COMPLETE,
        )

        logger.info(
            "historical_sync_completed instrument=%s timeframe=%s status=%s candles=%s "
            "completed=%s skipped=%s failed=%s gaps=%s",
            instrument_key,
            spec.id,
            summary.status,
            count,
            summary.chunks_completed,
            summary.chunks_skipped,
            summary.chunks_failed,
            summary.suspicious_gap_count,
        )
        return summary

    async def sync_range(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        from_date: date,
        to_date: date,
    ) -> int:
        """
        Fetch one explicit historical window (used by reconnect backfill).

        Coverage status is left untouched: this is a repair, not a full sync.
        """
        spec = resolve_timeframe(timeframe)
        if from_date > to_date:
            return 0
        total = 0
        max_retries = max(1, int(self.settings.historical_max_retries))
        for chunk in plan_chunks(timeframe=spec, from_date=from_date, to_date=to_date):
            ok, count, _ = await self._fetch_chunk_with_retries(
                instrument_key=instrument_key,
                spec=spec,
                from_d=chunk.from_date,
                to_d=chunk.to_date,
                max_retries=max_retries,
            )
            if ok:
                total += count
        if total:
            self.repo.update_coverage(instrument_key=instrument_key, timeframe=spec.id)
        return total

    async def sync_today_session(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        now: datetime | None = None,
    ) -> int:
        """
        Reconstruct today's session from the Upstox intraday endpoint.

        Completed intervals are stored CLOSED; only the interval containing the
        current IST time stays FORMING.
        """
        spec = resolve_timeframe(timeframe)
        current = ensure_utc(now or datetime.now(timezone.utc))
        logger.info(
            "today_sync_started instrument=%s timeframe=%s ist=%s",
            instrument_key,
            spec.id,
            to_ist(current).isoformat(),
        )
        raw = await self.client.get_intraday_candles_v3(
            instrument_key=instrument_key,
            unit=spec.unit,
            interval=spec.interval,
        )
        candles = normalize_upstox_payload(
            raw,
            instrument_key=instrument_key,
            timeframe=spec.id,
            source="upstox_intraday",
            is_closed=True,
        )
        candles = classify_forming(candles, spec, now=current)
        n = self.repo.bulk_upsert(candles)
        self.repo.update_coverage(instrument_key=instrument_key, timeframe=spec.id)
        logger.info(
            "today_sync_completed instrument=%s timeframe=%s upserted=%s forming=%s",
            instrument_key,
            spec.id,
            n,
            sum(1 for c in candles if not c.is_closed),
        )
        return n

    async def _fetch_chunk_with_retries(
        self,
        *,
        instrument_key: str,
        spec: TimeframeSpec,
        from_d: date,
        to_d: date,
        max_retries: int,
    ) -> tuple[bool, int, str | None]:
        delay = 1.0
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    "historical_chunk_started instrument=%s timeframe=%s from=%s to=%s attempt=%s",
                    instrument_key,
                    spec.id,
                    from_d.isoformat(),
                    to_d.isoformat(),
                    attempt,
                )
                raw = await self.client.get_historical_candles_v3(
                    instrument_key=instrument_key,
                    unit=spec.unit,
                    interval=spec.interval,
                    to_date=to_d.isoformat(),
                    from_date=from_d.isoformat(),
                )
                candles = normalize_upstox_payload(
                    raw,
                    instrument_key=instrument_key,
                    timeframe=spec.id,
                    source="upstox_historical",
                    is_closed=True,
                )
                n = self.repo.bulk_upsert(candles)
                logger.info(
                    "historical_chunk_completed instrument=%s timeframe=%s from=%s to=%s upserted=%s",
                    instrument_key,
                    spec.id,
                    from_d.isoformat(),
                    to_d.isoformat(),
                    n,
                )
                return True, n, None
            except UpstoxAPIError as exc:
                msg = f"{from_d}..{to_d}: {exc.message}"
                logger.warning(
                    "historical_chunk_failed instrument=%s timeframe=%s attempt=%s err=%s",
                    instrument_key,
                    spec.id,
                    attempt,
                    exc.message,
                )
                if exc.status_code == 401 or attempt >= max_retries:
                    return False, 0, msg
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
            except Exception as exc:  # noqa: BLE001 — chunk-level failure is recoverable
                msg = f"{from_d}..{to_d}: {type(exc).__name__}: {exc}"
                logger.warning(
                    "historical_chunk_failed instrument=%s timeframe=%s attempt=%s err=%s",
                    instrument_key,
                    spec.id,
                    attempt,
                    msg,
                )
                if attempt >= max_retries:
                    return False, 0, msg
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
        return False, 0, "retries exhausted"


def _as_utc_day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
