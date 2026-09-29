"""
Coverage verification — did the sync really get the data it asked for?

Trading days come from the provider's own 1D series, so weekends, exchange
holidays and special sessions are never mistaken for missing data, and no
holiday calendar is invented.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.core.market_session import candle_open, expected_candle_opens, to_ist
from app.core.timeframes import TimeframeSpec
from app.repositories.candle_repository import CandleRepository

logger = logging.getLogger(__name__)

# Gap classification
EXPECTED_NON_TRADING_PERIOD = "EXPECTED_NON_TRADING_PERIOD"
EXPECTED_GAP = "EXPECTED_GAP"
SUSPICIOUS_GAP = "SUSPICIOUS_GAP"


@dataclass(frozen=True, slots=True)
class Gap:
    from_ts: datetime
    to_ts: datetime
    missing_candles: int
    classification: str

    def as_dict(self) -> dict:
        return {
            "from": self.from_ts.isoformat(),
            "to": self.to_ts.isoformat(),
            "missing_candles": self.missing_candles,
            "classification": self.classification,
        }


@dataclass
class CoverageReport:
    instrument_key: str
    timeframe: str
    verifiable: bool
    reason: str = ""
    scanned_from: datetime | None = None
    scanned_to: datetime | None = None
    trading_days_checked: int = 0
    suspicious_gaps: list[Gap] = field(default_factory=list)

    @property
    def suspicious_gap_count(self) -> int:
        return len(self.suspicious_gaps)

    def as_dict(self) -> dict:
        return {
            "verifiable": self.verifiable,
            "reason": self.reason,
            "scanned_from": self.scanned_from.isoformat() if self.scanned_from else None,
            "scanned_to": self.scanned_to.isoformat() if self.scanned_to else None,
            "trading_days_checked": self.trading_days_checked,
            "suspicious_gap_count": self.suspicious_gap_count,
            "gaps": [g.as_dict() for g in self.suspicious_gaps[:20]],
        }


def detect_gaps(
    repo: CandleRepository,
    *,
    instrument_key: str,
    spec: TimeframeSpec,
    scan_days: int,
    now: datetime | None = None,
    scan_to: datetime | None = None,
) -> CoverageReport:
    """
    Look for missing candles inside real trading sessions.

    Only the most recent `scan_days` calendar days are scanned so a multi-year
    minute history never has to be walked in memory. `scan_to` bounds the scan
    for callers that are only responsible for part of the range (the historical
    pass stops at yesterday; today belongs to the intraday pass).
    """
    current = (scan_to or now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report = CoverageReport(
        instrument_key=instrument_key,
        timeframe=spec.id,
        verifiable=False,
    )

    if spec.unit not in {"minutes", "hours"}:
        # Daily+ series: a missing day cannot be distinguished from a holiday
        # without another provider source, so it is not flagged.
        report.reason = "Gap scan applies to intraday timeframes only"
        return report

    scan_from = current - timedelta(days=max(1, scan_days))
    report.scanned_from = scan_from
    report.scanned_to = current

    trading_days = repo.trading_days(
        instrument_key=instrument_key,
        from_ts=scan_from,
        to_ts=current,
    )
    if not trading_days:
        report.reason = (
            "No 1D candles stored for this instrument; trading days cannot be "
            "confirmed, so gaps are not classified"
        )
        return report

    stored = set(
        repo.get_timestamps(
            instrument_key=instrument_key,
            timeframe=spec.id,
            from_ts=scan_from,
            to_ts=current,
        )
    )

    report.verifiable = True
    today_ist = to_ist(current).date()

    for day in trading_days:
        expected = expected_candle_opens(day, spec)
        if not expected:
            continue
        # Ignore the candle still forming and anything after it.
        if day == today_ist:
            active_open = candle_open(current, spec)
            expected = [ts for ts in expected if ts < active_open]
            if not expected:
                continue

        report.trading_days_checked += 1
        missing_run: list[datetime] = []
        for ts in expected:
            if ts in stored:
                if missing_run:
                    report.suspicious_gaps.append(_make_gap(missing_run, spec))
                    missing_run = []
                continue
            missing_run.append(ts)
        if missing_run:
            report.suspicious_gaps.append(_make_gap(missing_run, spec))

    if report.suspicious_gaps:
        logger.warning(
            "historical_hole_detected instrument=%s timeframe=%s gaps=%s",
            instrument_key,
            spec.id,
            report.suspicious_gap_count,
        )
    return report


def _make_gap(run: list[datetime], spec: TimeframeSpec) -> Gap:
    from app.core.market_session import candle_end

    return Gap(
        from_ts=run[0],
        to_ts=candle_end(run[-1], spec),
        missing_candles=len(run),
        classification=SUSPICIOUS_GAP,
    )


def verify_earliest_reached(
    repo: CandleRepository,
    *,
    instrument_key: str,
    timeframe: str,
    requested_from: date,
    actual_earliest: datetime | None,
) -> tuple[bool, str]:
    """
    Confirm the sync really reached the requested provider start date.

    The first stored candle legitimately lands after `requested_from` when that
    date was a weekend or holiday, so the provider's own 1D series decides.
    """
    if actual_earliest is None:
        return False, "No candles stored"

    actual_day = to_ist(actual_earliest).date()
    if actual_day <= requested_from:
        return True, ""

    trading_days = repo.trading_days(
        instrument_key=instrument_key,
        from_ts=datetime.combine(requested_from, datetime.min.time(), tzinfo=timezone.utc),
        to_ts=datetime.combine(actual_day, datetime.max.time(), tzinfo=timezone.utc),
    )
    candidates = [d for d in trading_days if requested_from <= d < actual_day]
    if not candidates:
        # Nothing tradable between the request date and the first candle.
        return True, ""
    return (
        False,
        (
            f"Earliest stored candle {actual_day.isoformat()} is later than the first "
            f"trading day {candidates[0].isoformat()} on/after {requested_from.isoformat()}"
        ),
    )
