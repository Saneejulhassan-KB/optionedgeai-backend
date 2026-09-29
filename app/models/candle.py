"""
Persisted OHLCV candles — identity: instrument_key + timeframe + timestamp (UTC).
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UtcDateTime

# Source precedence: a lower-ranked source must never overwrite a finalized
# candle produced by a higher-ranked one.
SOURCE_HISTORICAL = "upstox_historical"
SOURCE_INTRADAY = "upstox_intraday"
SOURCE_WEBSOCKET = "websocket"

SOURCE_RANK: dict[str, int] = {
    SOURCE_WEBSOCKET: 1,
    SOURCE_INTRADAY: 2,
    SOURCE_HISTORICAL: 3,
}


def source_rank(source: str) -> int:
    return SOURCE_RANK.get(source, 0)


class Candle(Base):
    """Normalized historical / session candle stored for the trading foundation."""

    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint(
            "instrument_key",
            "timeframe",
            "timestamp",
            name="uq_candles_instrument_timeframe_ts",
        ),
        Index("ix_candles_instrument_timeframe_ts", "instrument_key", "timeframe", "timestamp"),
        Index("ix_candles_instrument", "instrument_key"),
        Index("ix_candles_timeframe", "timeframe"),
        Index("ix_candles_timestamp", "timestamp"),
    )

    # SQLite only auto-increments a column declared exactly INTEGER PRIMARY KEY.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )

    instrument_key: Mapped[str] = mapped_column(String(128), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    open: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    open_interest: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default=SOURCE_HISTORICAL)

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
