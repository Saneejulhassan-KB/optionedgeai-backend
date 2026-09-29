"""
Candles service — Upstox V3 historical + intraday → Flutter OHLCV list.

Flutter intervals (e.g. "5minute", "day") are mapped to Upstox V3 unit/interval.
When the range includes today (IST), historical + current-day intraday are merged.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.instruments import INSTRUMENT_NIFTY
from app.schemas.market import CandleOut, CandlesResponse
from app.services.upstox_client import UpstoxAPIError, UpstoxClient

IST = ZoneInfo("Asia/Kolkata")

# Flutter / common aliases → (unit, interval)
_INTERVAL_ALIASES: dict[str, tuple[str, str]] = {
    "1minute": ("minutes", "1"),
    "1min": ("minutes", "1"),
    "2minute": ("minutes", "2"),
    "3minute": ("minutes", "3"),
    "5minute": ("minutes", "5"),
    "5min": ("minutes", "5"),
    "10minute": ("minutes", "10"),
    "15minute": ("minutes", "15"),
    "30minute": ("minutes", "30"),
    "30min": ("minutes", "30"),
    "60minute": ("hours", "1"),
    "1hour": ("hours", "1"),
    "hour": ("hours", "1"),
    "day": ("days", "1"),
    "1day": ("days", "1"),
    "daily": ("days", "1"),
    "week": ("weeks", "1"),
    "1week": ("weeks", "1"),
    "month": ("months", "1"),
    "1month": ("months", "1"),
}


class CandlesService:
    """Business logic for GET /market/candles."""

    def __init__(self, access_token: str) -> None:
        self._client = UpstoxClient(access_token)

    async def get_candles(
        self,
        *,
        instrument_key: str | None,
        interval: str,
        from_date: str | None,
        to_date: str | None,
    ) -> CandlesResponse:
        key = (instrument_key or INSTRUMENT_NIFTY).strip()
        if not key:
            raise UpstoxAPIError("instrument key is required.", status_code=400)

        unit, interval_num = self._parse_interval(interval)
        today = datetime.now(IST).date()
        to_d = self._parse_date(to_date) if to_date else today
        from_d = self._parse_date(from_date) if from_date else (to_d - timedelta(days=2))

        if from_d > to_d:
            raise UpstoxAPIError("`from` must be on or before `to`.", status_code=400)

        rows: list[list[Any]] = []

        include_intraday = to_d >= today
        hist_to = min(to_d, today - timedelta(days=1)) if include_intraday else to_d

        if from_d <= hist_to:
            raw = await self._client.get_historical_candles_v3(
                instrument_key=key,
                unit=unit,
                interval=interval_num,
                to_date=hist_to.isoformat(),
                from_date=from_d.isoformat(),
            )
            rows.extend(self._extract_rows(raw))

        if include_intraday and from_d <= today:
            raw = await self._client.get_intraday_candles_v3(
                instrument_key=key,
                unit=unit,
                interval=interval_num,
            )
            rows.extend(self._extract_rows(raw))

        candles = self._rows_to_candles(rows)
        # Keep chronological ascending for Flutter engines (mock was ascending)
        candles.sort(key=lambda c: c.timestamp)

        return CandlesResponse(
            instrument_key=key,
            interval=f"{unit}/{interval_num}",
            from_date=from_d.isoformat(),
            to_date=to_d.isoformat(),
            candles=candles,
        )

    def _parse_interval(self, raw: str) -> tuple[str, str]:
        text = (raw or "").strip().lower().replace(" ", "")
        if not text:
            raise UpstoxAPIError("interval is required.", status_code=400)

        if text in _INTERVAL_ALIASES:
            return _INTERVAL_ALIASES[text]

        # Already V3 style: minutes/5 or minutes:5
        if "/" in text or ":" in text:
            sep = "/" if "/" in text else ":"
            unit, interval_num = text.split(sep, 1)
            unit = unit.strip()
            interval_num = interval_num.strip()
            if unit in {"minutes", "hours", "days", "weeks", "months"} and interval_num.isdigit():
                return unit, interval_num

        # Bare number → minutes
        if text.isdigit():
            return "minutes", text

        # e.g. 5m, 15m
        m = re.fullmatch(r"(\d+)m(?:in(?:ute)?s?)?", text)
        if m:
            return "minutes", m.group(1)

        raise UpstoxAPIError(
            f"Unsupported interval '{raw}'. "
            "Try 1minute, 5minute, 15minute, 30minute, day, week, month, "
            "or V3 form minutes/5.",
            status_code=400,
        )

    def _parse_date(self, raw: str) -> date:
        text = raw.strip()
        try:
            # Accept YYYY-MM-DD or ISO datetime
            if "T" in text:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(IST).date()
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise UpstoxAPIError(
                f"Invalid date '{raw}'. Use YYYY-MM-DD.",
                status_code=400,
            ) from exc

    def _extract_rows(self, raw: dict[str, Any]) -> list[list[Any]]:
        data = raw.get("data") or {}
        if not isinstance(data, dict):
            return []
        candles = data.get("candles") or []
        if not isinstance(candles, list):
            return []
        out: list[list[Any]] = []
        for row in candles:
            if isinstance(row, list) and len(row) >= 5:
                out.append(row)
        return out

    def _rows_to_candles(self, rows: list[list[Any]]) -> list[CandleOut]:
        seen: set[str] = set()
        out: list[CandleOut] = []
        for row in rows:
            ts = self._normalize_ts(row[0])
            if ts in seen:
                continue
            seen.add(ts)
            volume = int(row[5]) if len(row) > 5 and row[5] is not None else 0
            out.append(
                CandleOut(
                    timestamp=ts,
                    open=float(row[1] or 0),
                    high=float(row[2] or 0),
                    low=float(row[3] or 0),
                    close=float(row[4] or 0),
                    volume=volume,
                )
            )
        return out

    def _normalize_ts(self, value: Any) -> str:
        text = str(value)
        try:
            if "T" in text or "+" in text or text.endswith("Z"):
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            else:
                # Upstox sometimes returns "YYYY-MM-DD HH:MM:SS"
                dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt.isoformat()
        except ValueError:
            return text
