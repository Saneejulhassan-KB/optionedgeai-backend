"""
Candle repository — precedence-aware bulk upsert, range queries, chunk ledger.

Identity: instrument_key + timeframe + timestamp (UTC).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import case, delete, func, literal, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.candle import SOURCE_RANK, Candle, source_rank
from app.models.historical_chunk import CHUNK_COMPLETED, CHUNK_FAILED, HistoricalChunk
from app.models.historical_coverage import STATUS_REQUESTED, HistoricalCoverage
from app.services.candle_normalize import NormalizedCandle


class CandleRepository:
    """Persistence helpers for candles, coverage metadata and chunk progress."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ candles

    def bulk_upsert(
        self,
        candles: Sequence[NormalizedCandle],
        *,
        batch_size: int = 500,
    ) -> int:
        """
        Insert or update candles, honouring source precedence.

        A lower-precedence source (websocket) never overwrites a finalized
        candle written by a higher-precedence source (intraday/historical),
        and a closed candle never reverts to forming.
        """
        if not candles:
            return 0

        by_source: dict[str, list[NormalizedCandle]] = defaultdict(list)
        for candle in candles:
            by_source[candle.source].append(candle)

        touched = 0
        for source, group in by_source.items():
            touched += self._upsert_single_source(group, source, batch_size)
        self.db.commit()
        return touched

    def _upsert_single_source(
        self,
        candles: Sequence[NormalizedCandle],
        source: str,
        batch_size: int,
    ) -> int:
        incoming_rank = source_rank(source)
        rows = [
            {
                "instrument_key": c.instrument_key,
                "timeframe": c.timeframe,
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "open_interest": c.open_interest,
                "is_closed": c.is_closed,
                "source": c.source,
            }
            for c in candles
        ]

        touched = 0
        for i in range(0, len(rows), batch_size):
            chunk = rows[i : i + batch_size]
            stmt = sqlite_insert(Candle).values(chunk)
            existing_rank = case(
                SOURCE_RANK,
                value=Candle.source,
                else_=0,
            )
            upsert = stmt.on_conflict_do_update(
                index_elements=["instrument_key", "timeframe", "timestamp"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "open_interest": stmt.excluded.open_interest,
                    # A closed candle stays closed.
                    "is_closed": or_(Candle.is_closed, stmt.excluded.is_closed),
                    "source": stmt.excluded.source,
                    "updated_at": func.now(),
                },
                where=or_(
                    Candle.is_closed.is_(False),
                    literal(incoming_rank) >= existing_rank,
                ),
            )
            self.db.execute(upsert)
            touched += len(chunk)
        return touched

    def close_candles_before(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        before_ts: datetime,
    ) -> int:
        """Mark every still-forming candle that starts before `before_ts` as closed."""
        rows = list(
            self.db.scalars(
                select(Candle).where(
                    Candle.instrument_key == instrument_key,
                    Candle.timeframe == timeframe,
                    Candle.timestamp < before_ts,
                    Candle.is_closed.is_(False),
                )
            ).all()
        )
        for row in rows:
            row.is_closed = True
        if rows:
            self.db.commit()
        return len(rows)

    def get_range(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int | None = None,
    ) -> list[Candle]:
        """Return candles oldest→newest. If limit set, returns the *newest* N bars."""
        filters = [
            Candle.instrument_key == instrument_key,
            Candle.timeframe == timeframe,
        ]
        if from_ts is not None:
            filters.append(Candle.timestamp >= from_ts)
        if to_ts is not None:
            filters.append(Candle.timestamp <= to_ts)

        if limit is not None:
            rows = list(
                self.db.scalars(
                    select(Candle).where(*filters).order_by(Candle.timestamp.desc()).limit(limit)
                ).all()
            )
            rows.reverse()
            return rows

        return list(
            self.db.scalars(select(Candle).where(*filters).order_by(Candle.timestamp.asc())).all()
        )

    def get_timestamps(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[datetime]:
        """Timestamps only — used by gap detection without loading full rows."""
        filters = [
            Candle.instrument_key == instrument_key,
            Candle.timeframe == timeframe,
        ]
        if from_ts is not None:
            filters.append(Candle.timestamp >= from_ts)
        if to_ts is not None:
            filters.append(Candle.timestamp <= to_ts)
        rows = self.db.scalars(
            select(Candle.timestamp).where(*filters).order_by(Candle.timestamp.asc())
        ).all()
        return [
            ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc) for ts in rows
        ]

    def get_latest(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        closed_only: bool = False,
    ) -> Candle | None:
        filters = [
            Candle.instrument_key == instrument_key,
            Candle.timeframe == timeframe,
        ]
        if closed_only:
            filters.append(Candle.is_closed.is_(True))
        stmt = select(Candle).where(*filters).order_by(Candle.timestamp.desc()).limit(1)
        return self.db.scalars(stmt).first()

    def get_earliest(self, *, instrument_key: str, timeframe: str) -> Candle | None:
        stmt = (
            select(Candle)
            .where(
                Candle.instrument_key == instrument_key,
                Candle.timeframe == timeframe,
            )
            .order_by(Candle.timestamp.asc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def count(self, *, instrument_key: str, timeframe: str) -> int:
        stmt = select(func.count()).select_from(Candle).where(
            Candle.instrument_key == instrument_key,
            Candle.timeframe == timeframe,
        )
        return int(self.db.scalar(stmt) or 0)

    def delete_range(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> int:
        stmt = delete(Candle).where(
            Candle.instrument_key == instrument_key,
            Candle.timeframe == timeframe,
            Candle.timestamp >= from_ts,
            Candle.timestamp <= to_ts,
        )
        result = self.db.execute(stmt)
        self.db.commit()
        return int(result.rowcount or 0)

    # ----------------------------------------------------------------- coverage

    def get_or_create_coverage(
        self, *, instrument_key: str, timeframe: str
    ) -> HistoricalCoverage:
        stmt = select(HistoricalCoverage).where(
            HistoricalCoverage.instrument_key == instrument_key,
            HistoricalCoverage.timeframe == timeframe,
        )
        row = self.db.scalars(stmt).first()
        if row is not None:
            return row
        row = HistoricalCoverage(
            instrument_key=instrument_key,
            timeframe=timeframe,
            status=STATUS_REQUESTED,
            candle_count=0,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_coverage(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        status: str | None = None,
        requested_from: datetime | None = None,
        requested_to: datetime | None = None,
        last_error: str | None = None,
        suspicious_gap_count: int | None = None,
        missing_ranges: list[dict] | None = None,
        mark_success: bool = False,
    ) -> HistoricalCoverage:
        """Refresh observed range/count from candles and persist sync verdict."""
        cov = self.get_or_create_coverage(
            instrument_key=instrument_key, timeframe=timeframe
        )
        earliest = self.get_earliest(instrument_key=instrument_key, timeframe=timeframe)
        latest = self.get_latest(instrument_key=instrument_key, timeframe=timeframe)

        cov.earliest_timestamp = earliest.timestamp if earliest else None
        cov.latest_timestamp = latest.timestamp if latest else None
        cov.candle_count = self.count(instrument_key=instrument_key, timeframe=timeframe)

        if requested_from is not None:
            cov.requested_from = requested_from
        if requested_to is not None:
            cov.requested_to = requested_to
        if status is not None:
            cov.status = status
        if last_error is not None:
            cov.last_error = last_error[:512] if last_error else None
        if suspicious_gap_count is not None:
            cov.suspicious_gap_count = suspicious_gap_count
        if missing_ranges is not None:
            cov.missing_ranges = json.dumps(missing_ranges[:50])

        now = datetime.now(timezone.utc)
        cov.last_sync_at = now
        if mark_success:
            cov.last_successful_sync = now

        self.db.commit()
        self.db.refresh(cov)
        return cov

    def list_coverage(self) -> list[HistoricalCoverage]:
        return list(self.db.scalars(select(HistoricalCoverage)).all())

    # -------------------------------------------------------------- chunk ledger

    def completed_chunks(
        self, *, instrument_key: str, timeframe: str
    ) -> set[tuple[date, date]]:
        rows = self.db.execute(
            select(HistoricalChunk.from_date, HistoricalChunk.to_date).where(
                HistoricalChunk.instrument_key == instrument_key,
                HistoricalChunk.timeframe == timeframe,
                HistoricalChunk.status == CHUNK_COMPLETED,
            )
        ).all()
        return {(r[0], r[1]) for r in rows}

    def record_chunk(
        self,
        *,
        instrument_key: str,
        timeframe: str,
        from_date: date,
        to_date: date,
        status: str,
        candle_count: int = 0,
        last_error: str | None = None,
    ) -> HistoricalChunk:
        row = self.db.scalars(
            select(HistoricalChunk).where(
                HistoricalChunk.instrument_key == instrument_key,
                HistoricalChunk.timeframe == timeframe,
                HistoricalChunk.from_date == from_date,
                HistoricalChunk.to_date == to_date,
            )
        ).first()
        if row is None:
            row = HistoricalChunk(
                instrument_key=instrument_key,
                timeframe=timeframe,
                from_date=from_date,
                to_date=to_date,
                status=status,
                attempts=0,
            )
            self.db.add(row)

        row.status = status
        row.attempts = int(row.attempts or 0) + 1
        row.candle_count = candle_count
        row.last_error = last_error[:512] if last_error else None
        if status == CHUNK_COMPLETED:
            row.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(row)
        return row

    def chunk_status_counts(
        self, *, instrument_key: str, timeframe: str
    ) -> dict[str, int]:
        rows = self.db.execute(
            select(HistoricalChunk.status, func.count())
            .where(
                HistoricalChunk.instrument_key == instrument_key,
                HistoricalChunk.timeframe == timeframe,
            )
            .group_by(HistoricalChunk.status)
        ).all()
        out = {CHUNK_COMPLETED: 0, CHUNK_FAILED: 0}
        for status, count in rows:
            out[status] = int(count)
        return out

    def clear_chunks(self, *, instrument_key: str, timeframe: str) -> int:
        result = self.db.execute(
            delete(HistoricalChunk).where(
                HistoricalChunk.instrument_key == instrument_key,
                HistoricalChunk.timeframe == timeframe,
            )
        )
        self.db.commit()
        return int(result.rowcount or 0)

    def trading_days(
        self,
        *,
        instrument_key: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[date]:
        """
        Provider-observed trading days, taken from the stored 1D series.

        Using real daily candles avoids inventing a holiday calendar.
        """
        from app.core.market_session import to_ist

        stamps: Iterable[datetime] = self.get_timestamps(
            instrument_key=instrument_key,
            timeframe="1D",
            from_ts=from_ts,
            to_ts=to_ts,
        )
        return sorted({to_ist(ts).date() for ts in stamps})
