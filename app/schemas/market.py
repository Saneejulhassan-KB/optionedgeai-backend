"""
Market schemas — Flutter-facing shapes (Phase 5.1–5.4).
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MarketQuoteOut(BaseModel):
    """One instrument quote normalized for Flutter."""

    instrument_key: str
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    change: float = 0.0
    change_percent: float = 0.0
    timestamp: Optional[str] = None


class MarketQuotesResponse(BaseModel):
    """Response for GET /market/quote"""

    quotes: list[MarketQuoteOut] = Field(default_factory=list)


class OptionGreeksOut(BaseModel):
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    iv: float = 0.0


class OptionLegOut(BaseModel):
    instrument_key: str
    type: Literal["ce", "pe"]
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    oi: int = 0
    oi_change: int = 0
    iv: float = 0.0
    greeks: OptionGreeksOut = Field(default_factory=OptionGreeksOut)
    prev_close: float = 0.0


class OptionStrikeOut(BaseModel):
    strike: float
    ce: OptionLegOut
    pe: OptionLegOut
    moneyness: Literal["itm", "atm", "otm"] = "otm"
    is_atm: bool = False
    is_support: bool = False
    is_resistance: bool = False


class OptionChainOut(BaseModel):
    """
    Response for GET /market/option-chain

    Matches Flutter OptionChainData.
    """

    underlying: str
    spot: float
    expiry: str  # ISO date YYYY-MM-DD
    atm_strike: float
    pcr: float
    strikes: list[OptionStrikeOut] = Field(default_factory=list)


class OptionExpiriesOut(BaseModel):
    """Response for GET /market/option-expiries"""

    underlying: str
    expiries: list[str] = Field(default_factory=list)


class OptionGreekQuoteOut(BaseModel):
    """One instrument's Greeks + LTP from Upstox V3 option-greek API."""

    instrument_key: str
    ltp: float = 0.0
    volume: int = 0
    oi: int = 0
    close: float = 0.0  # previous close (cp)
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    iv: float = 0.0  # percent (e.g. 33.5), normalized for Flutter


class OptionGreeksResponse(BaseModel):
    """Response for GET /market/greeks"""

    greeks: list[OptionGreekQuoteOut] = Field(default_factory=list)


class CandleOut(BaseModel):
    """One OHLCV candle for Flutter."""

    timestamp: str  # ISO-8601
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class CandlesResponse(BaseModel):
    """Response for GET /market/candles"""

    instrument_key: str
    interval: str
    from_date: str
    to_date: str
    candles: list[CandleOut] = Field(default_factory=list)
