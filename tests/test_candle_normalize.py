"""Unit tests — candle normalization + validation + dedupe."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.candle_normalize import (
    CandleValidationError,
    normalize_upstox_payload,
    normalize_upstox_row,
)

IST = ZoneInfo("Asia/Kolkata")


def test_normalize_valid_row():
    c = normalize_upstox_row(
        ["2024-06-03 09:15:00", 100, 105, 99, 102, 10, 5],
        instrument_key="NSE_INDEX|Nifty 50",
        timeframe="5m",
        source="upstox_historical",
    )
    assert float(c.open) == 100
    assert float(c.high) == 105
    assert c.volume == 10
    assert c.open_interest == 5
    assert c.timestamp.tzinfo is not None


def test_reject_high_below_low():
    with pytest.raises(CandleValidationError):
        normalize_upstox_row(
            ["2024-06-03 09:15:00", 100, 90, 95, 102, 1],
            instrument_key="NSE_INDEX|Nifty 50",
            timeframe="5m",
            source="test",
        )


def test_payload_dedupes_overlapping_timestamps():
    raw = {
        "data": {
            "candles": [
                ["2024-06-03T09:15:00+05:30", 100, 105, 99, 102, 10],
                ["2024-06-03T09:15:00+05:30", 101, 106, 100, 103, 12],  # overlap
                ["2024-06-03T09:20:00+05:30", 103, 104, 102, 103, 8],
            ]
        }
    }
    out = normalize_upstox_payload(
        raw,
        instrument_key="NSE_INDEX|Nifty 50",
        timeframe="5m",
        source="test",
    )
    assert len(out) == 2
    assert float(out[0].close) == 103  # last wins
    assert out[0].timestamp < out[1].timestamp
