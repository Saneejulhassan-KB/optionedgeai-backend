"""Hole detection must find real gaps and ignore weekends/holidays."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.core.market_session import IST, expected_candle_opens, to_ist
from app.core.timeframes import resolve_timeframe
from app.repositories.candle_repository import CandleRepository
from app.services.coverage_validator import detect_gaps, verify_earliest_reached
from tests.conftest import make_candle

KEY = "NSE_INDEX|Nifty 50"
SPEC = resolve_timeframe("15m")

# Mon 10 Aug 2026 → Fri 14 Aug 2026 (Sat/Sun 15–16 Aug are non-trading).
WEEK = [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13)]
NOW = datetime(2026, 8, 13, 16, 0, tzinfo=IST)


def _seed_daily(repo: CandleRepository, days: list[date]) -> None:
    """1D candles are the provider's own record of which days actually traded."""
    repo.bulk_upsert(
        [
            make_candle(
                timeframe="1D",
                timestamp=datetime(d.year, d.month, d.day, 0, 0, tzinfo=IST),
            )
            for d in days
        ]
    )


def _seed_sessions(repo: CandleRepository, days: list[date], *, skip=()) -> None:
    candles = []
    for day in days:
        for ts in expected_candle_opens(day, SPEC):
            if to_ist(ts).strftime("%H:%M") in skip:
                continue
            candles.append(make_candle(timeframe=SPEC.id, timestamp=ts))
    repo.bulk_upsert(candles)


def test_complete_week_reports_no_gaps_and_ignores_the_weekend(db):
    repo = CandleRepository(db)
    _seed_daily(repo, WEEK)
    _seed_sessions(repo, WEEK)

    report = detect_gaps(repo, instrument_key=KEY, spec=SPEC, scan_days=10, now=NOW)

    assert report.verifiable is True
    assert report.trading_days_checked == len(WEEK)
    assert report.suspicious_gap_count == 0


def test_missing_intraday_block_is_flagged(db):
    repo = CandleRepository(db)
    _seed_daily(repo, WEEK)
    _seed_sessions(repo, WEEK, skip={"11:00", "11:15", "11:30"})

    report = detect_gaps(repo, instrument_key=KEY, spec=SPEC, scan_days=10, now=NOW)

    assert report.suspicious_gap_count == len(WEEK)
    gap = report.suspicious_gaps[0]
    assert gap.missing_candles == 3
    assert to_ist(gap.from_ts).strftime("%H:%M") == "11:00"


def test_exchange_holiday_is_not_a_gap(db):
    """A day with no 1D candle never traded, so its absence is expected."""
    repo = CandleRepository(db)
    holiday = date(2026, 8, 12)
    traded = [d for d in WEEK if d != holiday]

    _seed_daily(repo, traded)
    _seed_sessions(repo, traded)

    report = detect_gaps(repo, instrument_key=KEY, spec=SPEC, scan_days=10, now=NOW)

    assert report.suspicious_gap_count == 0
    assert report.trading_days_checked == len(traded)


def test_whole_missing_trading_day_is_flagged(db):
    repo = CandleRepository(db)
    _seed_daily(repo, WEEK)
    _seed_sessions(repo, [d for d in WEEK if d != date(2026, 8, 12)])

    report = detect_gaps(repo, instrument_key=KEY, spec=SPEC, scan_days=10, now=NOW)

    assert report.suspicious_gap_count == 1
    assert report.suspicious_gaps[0].missing_candles == len(
        expected_candle_opens(date(2026, 8, 12), SPEC)
    )


def test_unfinished_candles_of_the_current_day_are_not_gaps(db):
    repo = CandleRepository(db)
    _seed_daily(repo, WEEK)
    _seed_sessions(repo, WEEK[:-1])
    # Today only has data up to 11:00.
    repo.bulk_upsert(
        [
            make_candle(timeframe=SPEC.id, timestamp=ts)
            for ts in expected_candle_opens(WEEK[-1], SPEC)
            if to_ist(ts).hour < 11
        ]
    )

    midday = datetime(2026, 8, 13, 11, 5, tzinfo=IST)
    report = detect_gaps(repo, instrument_key=KEY, spec=SPEC, scan_days=10, now=midday)

    assert report.suspicious_gap_count == 0


def test_gaps_are_not_classified_without_a_daily_series(db):
    repo = CandleRepository(db)
    _seed_sessions(repo, WEEK)

    report = detect_gaps(repo, instrument_key=KEY, spec=SPEC, scan_days=10, now=NOW)

    assert report.verifiable is False
    assert report.suspicious_gap_count == 0
    assert "1D" in report.reason


def test_earliest_is_accepted_when_the_requested_day_was_a_holiday(db):
    repo = CandleRepository(db)
    _seed_daily(repo, [date(2026, 8, 10), date(2026, 8, 11)])

    ok, reason = verify_earliest_reached(
        repo,
        instrument_key=KEY,
        timeframe="15m",
        requested_from=date(2026, 8, 8),  # Saturday
        actual_earliest=datetime(2026, 8, 10, 9, 15, tzinfo=IST),
    )
    assert ok is True
    assert reason == ""


def test_earliest_is_rejected_when_a_trading_day_was_skipped(db):
    repo = CandleRepository(db)
    _seed_daily(repo, [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)])

    ok, reason = verify_earliest_reached(
        repo,
        instrument_key=KEY,
        timeframe="15m",
        requested_from=date(2026, 8, 10),
        actual_earliest=datetime(2026, 8, 12, 9, 15, tzinfo=IST),
    )
    assert ok is False
    assert "2026-08-10" in reason


def test_no_candles_can_never_verify_as_reached(db):
    ok, reason = verify_earliest_reached(
        CandleRepository(db),
        instrument_key=KEY,
        timeframe="15m",
        requested_from=date(2022, 1, 1),
        actual_earliest=None,
    )
    assert ok is False
    assert reason


def test_daily_timeframe_is_not_gap_scanned(db):
    report = detect_gaps(
        CandleRepository(db),
        instrument_key=KEY,
        spec=resolve_timeframe("1D"),
        scan_days=30,
        now=NOW,
    )
    assert report.verifiable is False
    assert report.suspicious_gap_count == 0
