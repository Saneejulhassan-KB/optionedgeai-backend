"""
Option Greeks service — Upstox V3 option-greek → Flutter-friendly list.
"""

from __future__ import annotations

from typing import Any

from app.core.instruments import INSTRUMENT_NIFTY
from app.schemas.market import OptionGreekQuoteOut, OptionGreeksResponse
from app.services.market_option_chain import OptionChainService
from app.services.upstox_client import UpstoxAPIError, UpstoxClient

# Upstox V3 option-greek supports max 50 keys per request
MAX_GREEK_KEYS: int = 50


class OptionGreeksService:
    """Business logic for GET /market/greeks."""

    def __init__(self, access_token: str) -> None:
        self._client = UpstoxClient(access_token)
        self._access_token = access_token

    async def get_greeks(self, keys: list[str] | None) -> OptionGreeksResponse:
        instrument_keys = await self._normalize_keys(keys)
        raw = await self._client.get_option_greeks(instrument_keys)
        data = raw.get("data") or {}
        if not isinstance(data, dict):
            raise UpstoxAPIError("Upstox greeks data missing.", status_code=502)

        by_token: dict[str, dict[str, Any]] = {}
        for _alias, row in data.items():
            if not isinstance(row, dict):
                continue
            token = str(row.get("instrument_token") or "")
            if token:
                by_token[token] = row

        out: list[OptionGreekQuoteOut] = []
        for key in instrument_keys:
            row = by_token.get(key)
            if row is None:
                row = self._find_fuzzy(data, key)
            if row is None:
                continue
            out.append(self._to_out(key, row))

        return OptionGreeksResponse(greeks=out)

    async def _normalize_keys(self, keys: list[str] | None) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in keys or []:
            key = raw.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(key)

        if cleaned:
            if len(cleaned) > MAX_GREEK_KEYS:
                raise UpstoxAPIError(
                    f"Too many keys (max {MAX_GREEK_KEYS}).",
                    status_code=400,
                )
            return cleaned

        # Default: ATM CE + PE from NIFTY current week (Greeks dashboard)
        chain_svc = OptionChainService(self._access_token)
        chain = await chain_svc.get_chain(underlying=INSTRUMENT_NIFTY, expiry="current_week")
        atm = next((s for s in chain.strikes if s.is_atm), None)
        if atm is None and chain.strikes:
            atm = min(chain.strikes, key=lambda s: abs(s.strike - chain.atm_strike))
        if atm is None:
            raise UpstoxAPIError("Could not resolve ATM strikes for default greeks.", status_code=502)

        defaults = [atm.ce.instrument_key, atm.pe.instrument_key]
        return [k for k in defaults if k]

    def _find_fuzzy(self, data: dict[str, Any], instrument_key: str) -> dict[str, Any] | None:
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

    def _to_out(self, instrument_key: str, row: dict[str, Any]) -> OptionGreekQuoteOut:
        raw_iv = float(row.get("iv") or 0)
        # V3 often returns IV as decimal (0.33); chain API used percent (33).
        # Normalize to percent for Flutter consistency.
        iv_percent = raw_iv * 100.0 if 0 < raw_iv <= 3.0 else raw_iv

        return OptionGreekQuoteOut(
            instrument_key=str(row.get("instrument_token") or instrument_key),
            ltp=float(row.get("last_price") or 0),
            volume=int(row.get("volume") or 0),
            oi=int(row.get("oi") or 0),
            close=float(row.get("cp") or 0),
            delta=float(row.get("delta") or 0),
            gamma=float(row.get("gamma") or 0),
            theta=float(row.get("theta") or 0),
            vega=float(row.get("vega") or 0),
            rho=float(row.get("rho") or 0),
            iv=iv_percent,
        )
