"""Pydantic schemas for /api/v1/market/* historical foundation endpoints."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class CoverageOut(BaseModel):
    instrument_key: str
    timeframe: str
    status: str
    candle_count: int = 0
    requested_from: Optional[str] = None
    requested_to: Optional[str] = None
    earliest: Optional[str] = None
    latest: Optional[str] = None
    suspicious_gap_count: int = 0
    last_sync_at: Optional[str] = None
    last_successful_sync: Optional[str] = None
    last_error: Optional[str] = None


class StoredCandleOut(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    open_interest: Optional[int] = None
    is_closed: bool = True
    source: str = "upstox_historical"


class StoredCandlesResponse(BaseModel):
    instrument_key: str
    timeframe: str
    from_ts: Optional[str] = None
    to_ts: Optional[str] = None
    count: int
    candles: list[StoredCandleOut] = Field(default_factory=list)


class SyncSeriesResult(BaseModel):
    instrument_key: str
    timeframe: str
    status: str  # REQUESTED | DOWNLOADING | PARTIAL | COMPLETE | FAILED
    reason: str = ""
    requested_from: Optional[str] = None
    requested_to: Optional[str] = None
    chunks_planned: int = 0
    chunks_skipped: int = 0
    chunks_completed: int = 0
    chunks_failed: int = 0
    candles_upserted: int = 0
    candle_count: int = 0
    suspicious_gap_count: int = 0
    earliest: Optional[str] = None
    latest: Optional[str] = None
    errors: list[str] = Field(default_factory=list)


class SyncResponse(BaseModel):
    status: str  # QUEUED | RUNNING | PARTIAL | COMPLETE | FAILED | DISABLED
    job_id: Optional[str] = None
    duration_seconds: Optional[float] = None
    results: list[SyncSeriesResult] = Field(default_factory=list)


class BootstrapResponse(BaseModel):
    status: str
    can_trade: bool = False
    server_time: str
    market_session: str
    instruments: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=list)
    trading_timeframes: list[str] = Field(default_factory=list)
    primary_timeframe: str = "5m"
    websocket: dict[str, Any] = Field(default_factory=dict)
    sync: dict[str, Any] = Field(default_factory=dict)
    readiness: dict[str, Any] = Field(default_factory=dict)
    historical_coverage: dict[str, Any] = Field(default_factory=dict)
    latest_candles: dict[str, Any] = Field(default_factory=dict)
    market_state: dict[str, Any] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)


class MarketStateResponse(BaseModel):
    """Passthrough wrapper — body is the MarketState dict."""

    state: dict[str, Any] = Field(default_factory=dict)


class VerificationResponse(BaseModel):
    server_time: str
    series: list[dict[str, Any]] = Field(default_factory=list)
