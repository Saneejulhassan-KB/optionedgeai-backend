"""
Market HTTP routes — Phase 5.1 Quote + Phase 5.2 Option Chain + Phase 5.3 Greeks + Phase 5.4 Candles.

Flutter:
  GET /market/quote?keys=...
  GET /market/option-chain?underlying=NSE_INDEX|Nifty 50&expiry=current_week
  GET /market/option-expiries?underlying=NSE_INDEX|Nifty 50
  GET /market/greeks?keys=...
  GET /market/candles?key=...&interval=5minute&from=YYYY-MM-DD&to=YYYY-MM-DD
  Authorization: Bearer <jwt>
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.instruments import INSTRUMENT_NIFTY, PRIMARY_QUOTE_KEYS
from app.dependencies.upstox_token import get_upstox_access_token
from app.schemas.market import (
    CandlesResponse,
    MarketQuotesResponse,
    OptionChainOut,
    OptionExpiriesOut,
    OptionGreeksResponse,
)
from app.services.market_candles import CandlesService
from app.services.market_greeks import OptionGreeksService
from app.services.market_option_chain import OptionChainService
from app.services.market_quote import MarketQuoteService
from app.services.upstox_client import UpstoxAPIError

router = APIRouter(prefix="/market", tags=["Market"])


@router.get(
    "/quote",
    response_model=MarketQuotesResponse,
    summary="Market quotes (JWT + Upstox token on server)",
)
async def get_market_quotes(
    keys: str | None = Query(
        default=None,
        description=(
            "Comma-separated instrument keys. "
            "Omit to use VIX + NIFTY + BANK NIFTY + SENSEX."
        ),
        examples=["NSE_INDEX|Nifty 50,NSE_INDEX|India VIX"],
    ),
    access_token: str = Depends(get_upstox_access_token),
) -> MarketQuotesResponse:
    """Backend → Upstox full quotes → Flutter-friendly JSON."""
    key_list: list[str] | None = None
    if keys:
        key_list = [part.strip() for part in keys.split(",") if part.strip()]

    service = MarketQuoteService(access_token)
    try:
        return await service.get_quotes(key_list)
    except UpstoxAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get(
    "/quote/defaults",
    response_model=dict,
    summary="List default quote instrument keys",
)
def quote_defaults() -> dict[str, list[str]]:
    """Helper for Flutter / docs — no auth required."""
    return {"keys": list(PRIMARY_QUOTE_KEYS)}


@router.get(
    "/option-chain",
    response_model=OptionChainOut,
    summary="Option chain for an underlying (JWT required)",
)
async def get_option_chain(
    underlying: str = Query(
        default=INSTRUMENT_NIFTY,
        description="Underlying instrument_key, e.g. NSE_INDEX|Nifty 50",
        examples=[INSTRUMENT_NIFTY],
    ),
    expiry: str = Query(
        default="current_week",
        description=(
            "YYYY-MM-DD or relative keyword: "
            "current_week, next_week, far_week, current_month, next_month, far_month"
        ),
        examples=["current_week", "2026-07-31"],
    ),
    access_token: str = Depends(get_upstox_access_token),
) -> OptionChainOut:
    """
    Backend → Upstox /v2/option/chain → normalized strikes with CE/PE + Greeks.

    Example:
      GET /market/option-chain?underlying=NSE_INDEX|Nifty 50&expiry=current_week
    """
    service = OptionChainService(access_token)
    try:
        return await service.get_chain(underlying=underlying, expiry=expiry)
    except UpstoxAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get(
    "/option-expiries",
    response_model=OptionExpiriesOut,
    summary="List option expiries for an underlying (JWT required)",
)
async def get_option_expiries(
    underlying: str = Query(
        default=INSTRUMENT_NIFTY,
        description="Underlying instrument_key",
    ),
    access_token: str = Depends(get_upstox_access_token),
) -> OptionExpiriesOut:
    """Useful for Flutter expiry picker (Priority 2 API)."""
    service = OptionChainService(access_token)
    try:
        return await service.list_expiries(underlying=underlying)
    except UpstoxAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get(
    "/greeks",
    response_model=OptionGreeksResponse,
    summary="Option Greeks for instrument keys (JWT required)",
)
async def get_option_greeks(
    keys: str | None = Query(
        default=None,
        description=(
            "Comma-separated FO instrument keys (max 50). "
            "Omit to use NIFTY ATM CE+PE for current week."
        ),
        examples=["NSE_FO|43885,NSE_FO|43886"],
    ),
    access_token: str = Depends(get_upstox_access_token),
) -> OptionGreeksResponse:
    """
    Backend → Upstox V3 /market-quote/option-greek → Flutter-friendly list.

    Example:
      GET /market/greeks?keys=NSE_FO|43885,NSE_FO|43886
    """
    key_list: list[str] | None = None
    if keys:
        key_list = [part.strip() for part in keys.split(",") if part.strip()]

    service = OptionGreeksService(access_token)
    try:
        return await service.get_greeks(key_list)
    except UpstoxAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get(
    "/candles",
    response_model=CandlesResponse,
    summary="OHLCV candles (JWT required)",
)
async def get_market_candles(
    key: str | None = Query(
        default=None,
        description="Instrument key. Default: NSE_INDEX|Nifty 50",
        examples=[INSTRUMENT_NIFTY],
    ),
    interval: str = Query(
        default="5minute",
        description=(
            "Candle size: 1minute, 5minute, 15minute, 30minute, day, week, month, "
            "or V3 form minutes/5"
        ),
        examples=["5minute", "day", "minutes/15"],
    ),
    from_: str | None = Query(
        default=None,
        alias="from",
        description="Start date YYYY-MM-DD (default: to − 2 days)",
        examples=["2026-07-22"],
    ),
    to: str | None = Query(
        default=None,
        description="End date YYYY-MM-DD (default: today IST)",
        examples=["2026-07-24"],
    ),
    access_token: str = Depends(get_upstox_access_token),
) -> CandlesResponse:
    """
    Backend → Upstox V3 historical (+ intraday if range includes today) → OHLCV list.

    Example:
      GET /market/candles?key=NSE_INDEX|Nifty 50&interval=5minute&from=2026-07-22&to=2026-07-24
    """
    service = CandlesService(access_token)
    try:
        return await service.get_candles(
            instrument_key=key,
            interval=interval,
            from_date=from_,
            to_date=to,
        )
    except UpstoxAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
