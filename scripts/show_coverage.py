"""Print the current coverage verification report (local database only)."""

from __future__ import annotations

from app.database.session import SessionLocal, init_db
from app.services.market_bootstrap import build_verification_report


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        for row in build_verification_report(db):
            print(
                f"{row['instrument_key']:22} {row['timeframe']:4} {row['sync_status']:9} "
                f"earliest={str(row['actual_earliest'])[:16]} "
                f"latest={str(row['actual_latest'])[:16]} "
                f"count={row['candle_count']:<7} "
                f"gaps={row['suspicious_gap_count']} "
                f"chunks_ok={row['chunks_completed']} "
                f"chunks_failed={row['chunks_failed']}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
