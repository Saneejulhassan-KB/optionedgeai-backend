"""
Completed/failed historical chunk ledger.

Lets an interrupted sync skip work that already succeeded instead of
re-downloading the whole provider range.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UtcDateTime

CHUNK_COMPLETED = "COMPLETED"
CHUNK_FAILED = "FAILED"


class HistoricalChunk(Base):
    """One requested [from_date, to_date] window for an instrument + timeframe."""

    __tablename__ = "historical_chunks"
    __table_args__ = (
        UniqueConstraint(
            "instrument_key",
            "timeframe",
            "from_date",
            "to_date",
            name="uq_historical_chunk_window",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    instrument_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CHUNK_COMPLETED)
    candle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    completed_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
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
