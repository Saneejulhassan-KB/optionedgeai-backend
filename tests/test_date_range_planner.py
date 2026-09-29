"""Unit tests — date-range planner (Upstox V3 chunk limits)."""

from datetime import date

import pytest

from app.services.date_range_planner import plan_chunks, provider_available_from


def test_plan_chunks_5m_monthly():
    chunks = plan_chunks(
        timeframe="5m",
        from_date=date(2024, 1, 1),
        to_date=date(2024, 3, 15),
    )
    assert chunks[0].from_date == date(2024, 1, 1)
    assert all((c.to_date - c.from_date).days + 1 <= 28 for c in chunks)
    assert chunks[-1].to_date == date(2024, 3, 15)
    # contiguous
    for a, b in zip(chunks, chunks[1:]):
        assert b.from_date == a.to_date.fromordinal(a.to_date.toordinal()) or True
        from datetime import timedelta

        assert b.from_date == a.to_date + timedelta(days=1)


def test_plan_chunks_clamps_to_provider_from():
    chunks = plan_chunks(
        timeframe="5m",
        from_date=date(2010, 1, 1),
        to_date=date(2022, 1, 10),
    )
    assert chunks[0].from_date == provider_available_from("5m")
    assert chunks[0].from_date == date(2022, 1, 1)


def test_plan_chunks_same_day():
    chunks = plan_chunks(
        timeframe="1D",
        from_date=date(2024, 6, 1),
        to_date=date(2024, 6, 1),
    )
    assert len(chunks) == 1
    assert chunks[0].from_date == chunks[0].to_date


def test_plan_chunks_invalid_range():
    with pytest.raises(ValueError):
        plan_chunks(
            timeframe="5m",
            from_date=date(2024, 6, 2),
            to_date=date(2024, 6, 1),
        )


def test_1d_available_from_2000():
    assert provider_available_from("1D") == date(2000, 1, 1)
