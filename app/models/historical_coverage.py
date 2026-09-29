"""
Historical coverage metadata — verified sync state per instrument/timeframe.

"The table has candles" is NOT the same as "history is complete", so the
requested window is stored alongside what was actually observed.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UtcDateTime

# Coverage lifecycle
STATUS_REQUESTED = "REQUESTED"
STATUS_DOWNLOADING = "DOWNLOADING"
STATUS_PARTIAL = "PARTIAL"
STATUS_COMPLETE = "COMPLETE"
STATUS_FAILED = "FAILED"

LEGACY_STATUS_MAP: dict[str, str] = {
    "pending": STATUS_REQUESTED,
    "syncing": STATUS_DOWNLOADING,
    "ready": STATUS_COMPLETE,
    "error": STATUS_FAILED,
    "degraded": STATUS_PARTIAL,
}


class HistoricalCoverage(Base):
    """Verified sync progress for one instrument + timeframe."""

    __tablename__ = "historical_coverage"
    __table_args__ = (
        UniqueConstraint(
            "instrument_key",
            "timeframe",
            name="uq_historical_coverage_instrument_timeframe",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    instrument_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="upstox")

    requested_from: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    requested_to: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)

    earliest_timestamp: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    latest_timestamp: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    candle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_REQUESTED)
    suspicious_gap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_ranges: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    last_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    last_successful_sync: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
