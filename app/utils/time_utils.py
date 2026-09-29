"""
Time helpers for Upstox token expiry.

Upstox access tokens expire at 03:30 AM IST on the next cutoff,
regardless of when they were issued (see Upstox Get Token docs).
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# India Standard Time — Upstox cutoff is defined in IST
IST: ZoneInfo = ZoneInfo("Asia/Kolkata")

# Magic number avoided: named constant for the documented cutoff
UPSTOX_TOKEN_CUTOFF_HOUR: int = 3
UPSTOX_TOKEN_CUTOFF_MINUTE: int = 30


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp."""
    return datetime.now(tz=timezone.utc)


def upstox_access_token_expires_at(now: datetime | None = None) -> datetime:
    """
    Compute when the current Upstox access token becomes invalid.

    Rules (from Upstox docs):
    - Always expires at 03:30 IST.
    - If issued before 03:30 IST today → expires today 03:30 IST.
    - If issued at/after 03:30 IST today → expires tomorrow 03:30 IST.
    """
    now_ist: datetime = (now or datetime.now(tz=IST)).astimezone(IST)
    cutoff: time = time(UPSTOX_TOKEN_CUTOFF_HOUR, UPSTOX_TOKEN_CUTOFF_MINUTE)
    expiry_date: date

    # Compare clock time only (both naive time objects)
    if now_ist.time() < cutoff:
        expiry_date = now_ist.date()
    else:
        expiry_date = now_ist.date() + timedelta(days=1)

    expires_ist: datetime = datetime.combine(expiry_date, cutoff, tzinfo=IST)
    return expires_ist.astimezone(timezone.utc)
