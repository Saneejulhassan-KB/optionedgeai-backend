"""
Authenticated end-to-end verification against the real Upstox API.

Usage (from the project root, with a logged-in user in the local database):

    python -m scripts.verify_live_sync              # configured instruments/timeframes
    python -m scripts.verify_live_sync --days 30    # bounded smoke run
    python -m scripts.verify_live_sync --instrument "NSE_INDEX|Nifty 50" --timeframe 5m

Never prints tokens or secrets.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import date, timedelta

from app.core.market_session import IST
from app.database.session import SessionLocal, init_db
from app.services.historical_download import HistoricalDownloadService
from app.services.market_bootstrap import (
    build_verification_report,
    configured_instruments,
    configured_timeframes,
)
from app.services.upstox_http import shutdown_upstox_http, startup_upstox_http


def _load_access_token() -> str:
    from datetime import datetime, timezone

    from app.models.oauth_token import OAuthToken

    db = SessionLocal()
    try:
        row = db.query(OAuthToken).order_by(OAuthToken.updated_at.desc()).first()
        if row is None:
            raise SystemExit("No Upstox token stored. Complete the OAuth login first.")
        expires = row.expires_at
        if expires is not None:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc):
                raise SystemExit("Stored Upstox token has expired. Log in again.")
        return row.access_token
    finally:
        db.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=0, help="limit history to N days")
    parser.add_argument("--instrument", action="append", default=None)
    parser.add_argument("--timeframe", action="append", default=None)
    args = parser.parse_args()

    init_db()
    await startup_upstox_http()

    token = _load_access_token()
    instruments = args.instrument or configured_instruments()
    timeframes = args.timeframe or configured_timeframes()
    force_from: date | None = None
    if args.days:
        from datetime import datetime

        force_from = datetime.now(IST).date() - timedelta(days=args.days)

    db = SessionLocal()
    started = time.monotonic()
    try:
        service = HistoricalDownloadService(db, token)
        # 1D first: daily candles define the real trading-day calendar.
        ordered = sorted(timeframes, key=lambda t: 0 if t == "1D" else 1)
        for key in instruments:
            for timeframe in ordered:
                t0 = time.monotonic()
                summary = await service.sync_full_history(
                    instrument_key=key, timeframe=timeframe, force_from=force_from
                )
                await service.sync_today_session(
                    instrument_key=key, timeframe=timeframe
                )
                print(
                    f"{key:26} {timeframe:4} {summary.status:9} "
                    f"candles={summary.candle_count:<7} "
                    f"chunks ok/skip/fail={summary.chunks_completed}/"
                    f"{summary.chunks_skipped}/{summary.chunks_failed} "
                    f"gaps={summary.suspicious_gap_count} "
                    f"{time.monotonic() - t0:6.1f}s "
                    f"{summary.reason}"
                )
        print(f"\ntotal duration: {time.monotonic() - started:.1f}s\n")

        print("VERIFICATION REPORT")
        print("-" * 110)
        for row in build_verification_report(db):
            print(
                f"{row['instrument_key']:26} {row['timeframe']:4} {row['sync_status']:9} "
                f"requested_from={str(row['requested_from'])[:10]} "
                f"earliest={str(row['actual_earliest'])[:16]} "
                f"latest={str(row['actual_latest'])[:16]} "
                f"count={row['candle_count']:<7} "
                f"gaps={row['suspicious_gap_count']}"
            )
    finally:
        db.close()
        await shutdown_upstox_http()


if __name__ == "__main__":
    asyncio.run(main())
