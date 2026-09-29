"""
Market quote service — fetch Upstox full quotes and normalize for Flutter.
"""

from __future__ import annotations

from typing import Any

from app.core.instruments import MAX_QUOTE_KEYS, PRIMARY_QUOTE_KEYS
from app.schemas.market import MarketQuoteOut, MarketQuotesResponse
from app.services.upstox_client import UpstoxAPIError, UpstoxClient


class MarketQuoteService:
    """Business logic for GET /market/quote."""

    def __init__(self, access_token: str) -> None:
        self._client = UpstoxClient(access_token)

    async def get_quotes(self, keys: list[str] | None) -> MarketQuotesResponse:
        """
        Fetch quotes for the given instrument keys.

        If keys is empty/None, use the primary Home universe (VIX + 3 indices).
        """
        instrument_keys = self._normalize_keys(keys)
        raw = await self._client.get_full_quotes(instrument_keys)
        data = raw.get("data") or {}
        if not isinstance(data, dict):
            raise UpstoxAPIError("Upstox quote data missing.", status_code=502)

        # Index Upstox rows by instrument_token for reliable lookup
        by_token: dict[str, dict[str, Any]] = {}
        for _alias, row in data.items():
            if not isinstance(row, dict):
                continue
            token = str(row.get("instrument_token") or "")
            if token:
                by_token[token] = row

        quotes: list[MarketQuoteOut] = []
        for key in instrument_keys:
            row = by_token.get(key)
            if row is None:
                # Some responses use colon aliases — try loose match
                row = self._find_row_fuzzy(data, key)
            if row is None:
                continue
            quotes.append(self._to_quote(key, row))

        return MarketQuotesResponse(quotes=quotes)

    def _normalize_keys(self, keys: list[str] | None) -> list[str]:
        if not keys:
            return list(PRIMARY_QUOTE_KEYS)

        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in keys:
            key = raw.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(key)

        if not cleaned:
            return list(PRIMARY_QUOTE_KEYS)
        if len(cleaned) > MAX_QUOTE_KEYS:
            raise UpstoxAPIError(
                f"Too many keys (max {MAX_QUOTE_KEYS}).",
                status_code=400,
            )
        return cleaned

    def _find_row_fuzzy(
        self,
        data: dict[str, Any],
        instrument_key: str,
    ) -> dict[str, Any] | None:
        """Fallback when Upstox aliases keys differently (pipe vs colon)."""
        needle = instrument_key.replace("|", ":")
        for alias, row in data.items():
            if not isinstance(row, dict):
                continue
            if alias == needle or alias == instrument_key:
                return row
            token = str(row.get("instrument_token") or "")
            if token.replace("|", ":") == needle:
                return row
        return None

    def _to_quote(self, instrument_key: str, row: dict[str, Any]) -> MarketQuoteOut:
        ohlc = row.get("ohlc") if isinstance(row.get("ohlc"), dict) else {}
        open_px = float(ohlc.get("open") or 0)
        high_px = float(ohlc.get("high") or 0)
        low_px = float(ohlc.get("low") or 0)
        close_px = float(ohlc.get("close") or 0)
        ltp = float(row.get("last_price") or 0)
        change = float(row.get("net_change") if row.get("net_change") is not None else (ltp - close_px))
        change_percent = (change / close_px * 100.0) if close_px else 0.0
        volume = int(row.get("volume") or 0)
        symbol = str(row.get("symbol") or instrument_key.split("|")[-1])
        timestamp = row.get("timestamp")
        ts = str(timestamp) if timestamp is not None else None

        return MarketQuoteOut(
            instrument_key=str(row.get("instrument_token") or instrument_key),
            symbol=symbol,
            ltp=ltp,
            open=open_px,
            high=high_px,
            low=low_px,
            close=close_px,
            volume=volume,
            change=change,
            change_percent=change_percent,
            timestamp=ts,
        )
