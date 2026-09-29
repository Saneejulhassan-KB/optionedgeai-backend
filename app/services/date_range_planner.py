"""
Date-range planner for Upstox Historical Candle Data V3.

Encodes documented per-request retrieval limits so callers never send
an oversized range that Upstox rejects as invalid.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.core.timeframes import TimeframeSpec, resolve_timeframe


@dataclass(frozen=True, slots=True)
class DateChunk:
    """One inclusive [from_date, to_date] request window."""

    from_date: date
    to_date: date


def plan_chunks(
    *,
    timeframe: str | TimeframeSpec,
    from_date: date,
    to_date: date,
    chunk_days: int | None = None,
) -> list[DateChunk]:
    """
    Split [from_date, to_date] into Upstox-safe chunks (oldest → newest).

    Raises ValueError on invalid ranges.
    """
    spec = timeframe if isinstance(timeframe, TimeframeSpec) else resolve_timeframe(timeframe)
    if from_date > to_date:
        raise ValueError("`from_date` must be on or before `to_date`")

    # Clamp to provider availability for this timeframe family.
    start = max(from_date, spec.provider_available_from)
    if start > to_date:
        return []

    step = chunk_days if chunk_days is not None else spec.max_chunk_days
    if step < 1:
        raise ValueError("chunk_days must be >= 1")

    chunks: list[DateChunk] = []
    cursor = start
    while cursor <= to_date:
        end = min(cursor + timedelta(days=step - 1), to_date)
        chunks.append(DateChunk(from_date=cursor, to_date=end))
        cursor = end + timedelta(days=1)
    return chunks


def provider_available_from(timeframe: str | TimeframeSpec) -> date:
    spec = timeframe if isinstance(timeframe, TimeframeSpec) else resolve_timeframe(timeframe)
    return spec.provider_available_from
