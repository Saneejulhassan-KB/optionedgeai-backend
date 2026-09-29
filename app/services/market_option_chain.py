"""
Option chain service — Upstox put/call chain → Flutter OptionChainData shape.
"""

from __future__ import annotations

from typing import Any

from app.core.instruments import INSTRUMENT_NIFTY
from app.schemas.market import (
    OptionChainOut,
    OptionExpiriesOut,
    OptionGreeksOut,
    OptionLegOut,
    OptionStrikeOut,
)
from app.services.upstox_client import UpstoxAPIError, UpstoxClient

# Prefer concrete nearest expiry when Upstox relative keywords return empty.
DEFAULT_EXPIRY_KEYWORD: str = "current_week"
RELATIVE_EXPIRY_KEYWORDS: frozenset[str] = frozenset(
    {
        "current_week",
        "next_week",
        "far_week",
        "current_month",
        "next_month",
        "far_month",
    }
)


class OptionChainService:
    """Business logic for /market/option-chain and /market/option-expiries."""

    def __init__(self, access_token: str) -> None:
        self._client = UpstoxClient(access_token)

    async def get_chain(
        self,
        *,
        underlying: str | None,
        expiry: str | None,
    ) -> OptionChainOut:
        instrument_key = (underlying or INSTRUMENT_NIFTY).strip()
        if not instrument_key:
            raise UpstoxAPIError("underlying is required.", status_code=400)

        requested = (expiry or DEFAULT_EXPIRY_KEYWORD).strip() or DEFAULT_EXPIRY_KEYWORD
        expiry_date = await self._resolve_expiry(instrument_key, requested)

        raw = await self._client.get_option_chain(
            instrument_key=instrument_key,
            expiry_date=expiry_date,
        )
        rows = raw.get("data")
        if not isinstance(rows, list) or not rows:
            raise UpstoxAPIError(
                f"Upstox returned an empty option chain for expiry={expiry_date}.",
                status_code=502,
            )

        return self._normalize(instrument_key=instrument_key, rows=rows)

    async def list_expiries(self, *, underlying: str | None) -> OptionExpiriesOut:
        instrument_key = (underlying or INSTRUMENT_NIFTY).strip()
        if not instrument_key:
            raise UpstoxAPIError("underlying is required.", status_code=400)

        raw = await self._client.get_option_contracts(instrument_key=instrument_key)
        data = raw.get("data")
        expiries: list[str] = []
        if isinstance(data, list):
            seen: set[str] = set()
            for row in data:
                if not isinstance(row, dict):
                    continue
                exp = row.get("expiry")
                if exp is None:
                    continue
                exp_s = str(exp)[:10]
                if exp_s and exp_s not in seen:
                    seen.add(exp_s)
                    expiries.append(exp_s)
            expiries.sort()
        return OptionExpiriesOut(underlying=instrument_key, expiries=expiries)

    async def _resolve_expiry(self, instrument_key: str, requested: str) -> str:
        """
        Upstox relative keywords (current_week, ...) sometimes return [].
        Fall back to the soonest concrete expiry from /option/contract.
        """
        if requested not in RELATIVE_EXPIRY_KEYWORDS:
            return requested

        probe = await self._client.get_option_chain(
            instrument_key=instrument_key,
            expiry_date=requested,
        )
        data = probe.get("data")
        if isinstance(data, list) and data:
            return requested

        expiries = await self.list_expiries(underlying=instrument_key)
        if not expiries.expiries:
            raise UpstoxAPIError(
                "No option expiries found for this underlying.",
                status_code=502,
            )

        idx = 0
        if requested in {"next_week", "next_month"}:
            idx = min(1, len(expiries.expiries) - 1)
        elif requested in {"far_week", "far_month"}:
            idx = min(2, len(expiries.expiries) - 1)
        return expiries.expiries[idx]

    def _normalize(self, *, instrument_key: str, rows: list[Any]) -> OptionChainOut:
        first = rows[0] if isinstance(rows[0], dict) else {}
        spot = float(first.get("underlying_spot_price") or 0)
        expiry = str(first.get("expiry") or "")[:10]
        underlying = str(first.get("underlying_key") or instrument_key)

        strike_prices = [
            float(r.get("strike_price") or 0)
            for r in rows
            if isinstance(r, dict)
        ]
        atm = min(strike_prices, key=lambda s: abs(s - spot)) if strike_prices and spot else 0.0

        total_ce_oi = 0
        total_pe_oi = 0
        strikes: list[OptionStrikeOut] = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            strike = float(row.get("strike_price") or 0)
            ce = self._leg(row.get("call_options"), option_type="ce")
            pe = self._leg(row.get("put_options"), option_type="pe")
            total_ce_oi += ce.oi
            total_pe_oi += pe.oi

            is_atm = abs(strike - atm) < 1e-9
            if is_atm:
                moneyness: str = "atm"
            elif strike < spot:
                moneyness = "itm"
            else:
                moneyness = "otm"

            strikes.append(
                OptionStrikeOut(
                    strike=strike,
                    ce=ce,
                    pe=pe,
                    moneyness=moneyness,  # type: ignore[arg-type]
                    is_atm=is_atm,
                )
            )

        strikes.sort(key=lambda s: s.strike)
        pcr = (total_pe_oi / total_ce_oi) if total_ce_oi > 0 else 0.0

        return OptionChainOut(
            underlying=underlying,
            spot=spot,
            expiry=expiry,
            atm_strike=atm,
            pcr=round(pcr, 4),
            strikes=strikes,
        )

    def _leg(self, raw: Any, *, option_type: str) -> OptionLegOut:
        if not isinstance(raw, dict):
            return OptionLegOut(instrument_key="", type=option_type)  # type: ignore[arg-type]

        market = raw.get("market_data") if isinstance(raw.get("market_data"), dict) else {}
        greeks_raw = raw.get("option_greeks") if isinstance(raw.get("option_greeks"), dict) else {}

        oi = int(market.get("oi") or 0)
        prev_oi = int(market.get("prev_oi") or 0)
        iv = float(greeks_raw.get("iv") or 0)

        greeks = OptionGreeksOut(
            delta=float(greeks_raw.get("delta") or 0),
            gamma=float(greeks_raw.get("gamma") or 0),
            theta=float(greeks_raw.get("theta") or 0),
            vega=float(greeks_raw.get("vega") or 0),
            rho=float(greeks_raw.get("rho") or 0),
            iv=iv,
        )

        return OptionLegOut(
            instrument_key=str(raw.get("instrument_key") or ""),
            type=option_type,  # type: ignore[arg-type]
            ltp=float(market.get("ltp") or 0),
            bid=float(market.get("bid_price") or 0),
            ask=float(market.get("ask_price") or 0),
            volume=int(market.get("volume") or 0),
            oi=oi,
            oi_change=oi - prev_oi,
            iv=iv,
            greeks=greeks,
            prev_close=float(market.get("close_price") or 0),
        )
