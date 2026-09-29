"""Shared fixtures: an isolated in-memory SQLite database per test."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers every table on Base.metadata
from app.database.base import Base
from app.services.candle_normalize import NormalizedCandle


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_candle(
    *,
    instrument_key: str = "NSE_INDEX|Nifty 50",
    timeframe: str = "5m",
    timestamp: datetime,
    price: float = 100.0,
    volume: int = 0,
    is_closed: bool = True,
    source: str = "upstox_historical",
) -> NormalizedCandle:
    p = Decimal(str(price))
    return NormalizedCandle(
        instrument_key=instrument_key,
        timeframe=timeframe,
        timestamp=timestamp,
        open=p,
        high=p + Decimal("1"),
        low=p - Decimal("1"),
        close=p,
        volume=volume,
        open_interest=None,
        is_closed=is_closed,
        source=source,
    )


def make_series(
    count: int,
    *,
    end: datetime,
    step_minutes: int = 5,
    instrument_key: str = "NSE_INDEX|Nifty 50",
    timeframe: str = "5m",
    start_price: float = 100.0,
) -> list[NormalizedCandle]:
    """`count` consecutive candles ending at `end` (exclusive of `end`)."""
    out: list[NormalizedCandle] = []
    for i in range(count):
        ts = end - timedelta(minutes=step_minutes * (count - i))
        out.append(
            make_candle(
                instrument_key=instrument_key,
                timeframe=timeframe,
                timestamp=ts.astimezone(timezone.utc),
                price=start_price + (i % 20) * 0.5,
                volume=1000,
            )
        )
    return out
