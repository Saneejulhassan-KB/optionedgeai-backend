"""
Single source of truth for Indian market session rules and candle boundaries.

All persistence is UTC. Asia/Kolkata is used *only* for session/boundary math.
Trading days are never invented here: callers supply provider-observed trading
days (e.g. from the 1D candle series) when they need holiday-aware logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.timeframes import TimeframeSpec

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

# NSE/BSE regular equity-derivatives session (Asia/Kolkata).
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def ensure_utc(value: datetime) -> datetime:
    """Normalize any datetime to timezone-aware UTC (naive is assumed UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_ist(value: datetime) -> datetime:
    return ensure_utc(value).astimezone(IST)


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def session_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Return (open, close) of the IST trading session for `day`, in UTC."""
    open_ist = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    close_ist = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
    return open_ist.astimezone(UTC), close_ist.astimezone(UTC)


def is_within_session(value: datetime) -> bool:
    local = to_ist(value)
    if is_weekend(local.date()):
        return False
    return MARKET_OPEN <= local.time() < MARKET_CLOSE


def candle_open(value: datetime, spec: TimeframeSpec) -> datetime:
    """
    Floor a timestamp to its candle open (UTC), anchored on the IST session.

    Intraday buckets are anchored at 09:15 IST so a 3m candle is
    09:15–09:18, never 09:16–09:19.
    """
    local = to_ist(value)

    if spec.unit in {"minutes", "hours"}:
        step_minutes = int(spec.interval) * (60 if spec.unit == "hours" else 1)
        anchor = datetime.combine(local.date(), MARKET_OPEN, tzinfo=IST)
        if local < anchor:
            # Pre-open ticks belong to the first bucket of the session.
            return anchor.astimezone(UTC)
        elapsed = int((local - anchor).total_seconds() // 60)
        bucket = (elapsed // step_minutes) * step_minutes
        return (anchor + timedelta(minutes=bucket)).astimezone(UTC)

    if spec.unit == "days":
        return datetime.combine(local.date(), time(0, 0), tzinfo=IST).astimezone(UTC)

    # weeks / months are not used for live bucketing; floor to the day.
    return datetime.combine(local.date(), time(0, 0), tzinfo=IST).astimezone(UTC)


def candle_end(open_ts: datetime, spec: TimeframeSpec) -> datetime:
    """Exclusive end of the candle that starts at `open_ts` (UTC)."""
    start = ensure_utc(open_ts)
    if spec.unit == "minutes":
        end = start + timedelta(minutes=int(spec.interval))
    elif spec.unit == "hours":
        end = start + timedelta(hours=int(spec.interval))
    elif spec.unit == "days":
        end = start + timedelta(days=1)
    elif spec.unit == "weeks":
        end = start + timedelta(weeks=int(spec.interval))
    else:
        end = start + timedelta(days=31)

    if spec.unit in {"minutes", "hours"}:
        # An intraday candle can never extend past the session close.
        _, session_close = session_bounds_utc(to_ist(start).date())
        end = min(end, session_close)
    return end


def is_candle_closed(
    open_ts: datetime,
    spec: TimeframeSpec,
    *,
    now: datetime | None = None,
) -> bool:
    """True when the candle beginning at `open_ts` has finished."""
    current = ensure_utc(now or datetime.now(UTC))
    return current >= candle_end(open_ts, spec)


def expected_candle_opens(day: date, spec: TimeframeSpec) -> list[datetime]:
    """
    Candle opens expected during one trading session (UTC).

    Caller must already know `day` was a trading day; this does not guess.
    """
    if spec.unit not in {"minutes", "hours"}:
        return [datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(UTC)]

    step = int(spec.interval) * (60 if spec.unit == "hours" else 1)
    open_utc, close_utc = session_bounds_utc(day)
    out: list[datetime] = []
    cursor = open_utc
    while cursor < close_utc:
        out.append(cursor)
        cursor += timedelta(minutes=step)
    return out


@dataclass(frozen=True, slots=True)
class SessionPhase:
    """Where the IST clock currently sits relative to the trading session."""

    phase: str  # PRE_OPEN | OPEN | CLOSED | NON_TRADING_DAY
    trading_day: date


def session_phase(now: datetime | None = None) -> SessionPhase:
    local = to_ist(now or datetime.now(UTC))
    day = local.date()
    if is_weekend(day):
        return SessionPhase("NON_TRADING_DAY", day)
    if local.time() < MARKET_OPEN:
        return SessionPhase("PRE_OPEN", day)
    if local.time() >= MARKET_CLOSE:
        return SessionPhase("CLOSED", day)
    return SessionPhase("OPEN", day)
