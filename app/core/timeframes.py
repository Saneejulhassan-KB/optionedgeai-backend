"""
Canonical timeframes for historical + live candles.

Maps Flutter/config aliases → Upstox V3 (unit, interval) and documents
provider availability windows from Upstox Historical Candle Data V3 docs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable


@dataclass(frozen=True, slots=True)
class TimeframeSpec:
    """One supported candle size."""

    id: str  # canonical: 1m, 3m, 5m, 15m, 30m, 1h, 1D
    unit: str  # Upstox: minutes | hours | days | weeks | months
    interval: str  # numeric string
    # Earliest date Upstox documents as available for this unit family.
    provider_available_from: date
    # Max calendar days per single historical request (conservative vs docs).
    max_chunk_days: int


# Official V3 (Upstox docs):
# - minutes/hours available from Jan 2022; 1–15m → ~1 month/request;
#   >15m and hours → ~1 quarter/request
# - days/weeks/months available from Jan 2000; days → ~1 decade/request
_MINUTE_FROM = date(2022, 1, 1)
_DAILY_FROM = date(2000, 1, 1)

TIMEFRAMES: dict[str, TimeframeSpec] = {
    "1m": TimeframeSpec("1m", "minutes", "1", _MINUTE_FROM, 28),
    "3m": TimeframeSpec("3m", "minutes", "3", _MINUTE_FROM, 28),
    "5m": TimeframeSpec("5m", "minutes", "5", _MINUTE_FROM, 28),
    "15m": TimeframeSpec("15m", "minutes", "15", _MINUTE_FROM, 28),
    "30m": TimeframeSpec("30m", "minutes", "30", _MINUTE_FROM, 90),
    "1h": TimeframeSpec("1h", "hours", "1", _MINUTE_FROM, 90),
    "1D": TimeframeSpec("1D", "days", "1", _DAILY_FROM, 3650),
}

_ALIASES: dict[str, str] = {
    "1m": "1m",
    "1minute": "1m",
    "1min": "1m",
    "minutes/1": "1m",
    "3m": "3m",
    "3minute": "3m",
    "3min": "3m",
    "minutes/3": "3m",
    "5m": "5m",
    "5minute": "5m",
    "5min": "5m",
    "minutes/5": "5m",
    "15m": "15m",
    "15minute": "15m",
    "15min": "15m",
    "minutes/15": "15m",
    "30m": "30m",
    "30minute": "30m",
    "30min": "30m",
    "minutes/30": "30m",
    "1h": "1h",
    "1hour": "1h",
    "60minute": "1h",
    "hour": "1h",
    "hours/1": "1h",
    "1d": "1D",
    "1D": "1D",
    "day": "1D",
    "1day": "1D",
    "daily": "1D",
    "days/1": "1D",
}


def resolve_timeframe(raw: str) -> TimeframeSpec:
    """Resolve a user/config interval string to a TimeframeSpec."""
    key = (raw or "").strip().replace(" ", "")
    if not key:
        raise ValueError("timeframe is required")
    # Preserve 1D casing via alias table
    alias = _ALIASES.get(key) or _ALIASES.get(key.lower())
    if alias is None:
        raise ValueError(
            f"Unsupported timeframe '{raw}'. "
            f"Supported: {', '.join(TIMEFRAMES)}"
        )
    return TIMEFRAMES[alias]


def parse_timeframe_list(raw: str | Iterable[str]) -> list[TimeframeSpec]:
    """Parse comma-separated timeframe ids from settings."""
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        parts = list(raw)
    out: list[TimeframeSpec] = []
    seen: set[str] = set()
    for part in parts:
        spec = resolve_timeframe(part)
        if spec.id not in seen:
            seen.add(spec.id)
            out.append(spec)
    return out
