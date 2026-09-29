"""
Low-level HTTP client for Upstox REST APIs.

Why this file exists
--------------------
All outbound calls to api.upstox.com go through here so we:
  - attach the user's Upstox access_token once
  - share timeouts / error handling
  - never leak tokens into route responses
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.services.upstox_http import upstox_http_slot


class UpstoxAPIError(Exception):
    """Upstox returned a non-success response."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UpstoxClient:
    """Thin async wrapper around Upstox REST."""

    def __init__(
        self,
        access_token: str,
        settings: Optional[Settings] = None,
    ) -> None:
        self.access_token = access_token
        self.settings = settings or get_settings()
        self._base = self.settings.upstox_base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }

    async def get_full_quotes(self, instrument_keys: list[str]) -> dict[str, Any]:
        """
        GET /v2/market-quote/quotes?instrument_key=key1,key2

        Returns the raw Upstox JSON body (status + data).
        """
        if not instrument_keys:
            raise UpstoxAPIError("instrument_keys must not be empty.", status_code=400)

        url = f"{self._base}/v2/market-quote/quotes"
        params = {"instrument_key": ",".join(instrument_keys)}

        async with upstox_http_slot() as client:
            response = await client.get(url, headers=self._headers(), params=params)

        return self._parse_response(response, label="quote")

    async def get_option_chain(
        self,
        *,
        instrument_key: str,
        expiry_date: str,
    ) -> dict[str, Any]:
        """
        GET /v2/option/chain?instrument_key=...&expiry_date=...

        expiry_date: YYYY-MM-DD or relative keyword (current_week, next_week, ...).
        """
        url = f"{self._base}/v2/option/chain"
        params = {
            "instrument_key": instrument_key,
            "expiry_date": expiry_date,
        }
        async with upstox_http_slot() as client:
            response = await client.get(url, headers=self._headers(), params=params)
        return self._parse_response(response, label="option chain")

    async def get_option_contracts(
        self,
        *,
        instrument_key: str,
        expiry_date: str | None = None,
    ) -> dict[str, Any]:
        """GET /v2/option/contract — used to list available expiries."""
        url = f"{self._base}/v2/option/contract"
        params: dict[str, str] = {"instrument_key": instrument_key}
        if expiry_date:
            params["expiry_date"] = expiry_date
        async with upstox_http_slot() as client:
            response = await client.get(url, headers=self._headers(), params=params)
        return self._parse_response(response, label="option contracts")

    async def get_option_greeks(self, instrument_keys: list[str]) -> dict[str, Any]:
        """
        GET /v3/market-quote/option-greek?instrument_key=key1,key2

        Upstox documents this under Market Quote V3 (max 50 keys).
        """
        if not instrument_keys:
            raise UpstoxAPIError("instrument_keys must not be empty.", status_code=400)

        url = f"{self._base}/v3/market-quote/option-greek"
        params = {"instrument_key": ",".join(instrument_keys)}
        async with upstox_http_slot() as client:
            response = await client.get(url, headers=self._headers(), params=params)
        return self._parse_response(response, label="option greeks")

    async def get_historical_candles_v3(
        self,
        *,
        instrument_key: str,
        unit: str,
        interval: str,
        to_date: str,
        from_date: str | None = None,
    ) -> dict[str, Any]:
        """
        GET /v3/historical-candle/{key}/{unit}/{interval}/{to_date}/{from_date}

        unit: minutes | hours | days | weeks | months
        interval: numeric string (e.g. "5" for 5 minutes)
        dates: YYYY-MM-DD
        """
        from urllib.parse import quote

        encoded = quote(instrument_key, safe="")
        path = f"{self._base}/v3/historical-candle/{encoded}/{unit}/{interval}/{to_date}"
        if from_date:
            path = f"{path}/{from_date}"
        async with upstox_http_slot() as client:
            response = await client.get(path, headers=self._headers())
        return self._parse_response(response, label="historical candles")

    async def get_intraday_candles_v3(
        self,
        *,
        instrument_key: str,
        unit: str,
        interval: str,
    ) -> dict[str, Any]:
        """
        GET /v3/historical-candle/intraday/{key}/{unit}/{interval}

        Current trading day only.
        """
        from urllib.parse import quote

        encoded = quote(instrument_key, safe="")
        url = (
            f"{self._base}/v3/historical-candle/intraday/"
            f"{encoded}/{unit}/{interval}"
        )
        async with upstox_http_slot() as client:
            response = await client.get(url, headers=self._headers())
        return self._parse_response(response, label="intraday candles")

    def _parse_response(self, response: httpx.Response, *, label: str) -> dict[str, Any]:
        if response.status_code == 401:
            raise UpstoxAPIError(
                "Upstox rejected the access token. Please login again.",
                status_code=401,
            )
        if response.status_code >= 400:
            raise UpstoxAPIError(
                f"Upstox {label} API failed ({response.status_code}): {response.text}",
                status_code=502,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise UpstoxAPIError(
                f"Unexpected Upstox {label} response type.",
                status_code=502,
            )
        return payload
