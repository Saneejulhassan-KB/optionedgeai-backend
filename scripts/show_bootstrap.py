"""Print the live bootstrap verdict against the real local store (no secrets)."""

from __future__ import annotations

import json

from app.database.session import SessionLocal, init_db
from app.services.market_bootstrap import build_bootstrap_payload


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        payload = build_bootstrap_payload(db)
        print("status     :", payload["status"])
        print("can_trade  :", payload["can_trade"])
        print("session    :", payload["market_session"])
        print("websocket  :", payload["websocket"]["status"])
        print("quality    :", json.dumps(payload["data_quality"]))
        for instrument, readiness in payload["readiness"].items():
            print(f"\n{instrument}")
            print("  status   :", readiness["status"], "| can_trade:", readiness["can_trade"])
            for flag in (
                "historical_coverage_ready",
                "today_session_ready",
                "required_indicators_ready",
                "market_structure_ready",
                "no_critical_data_gaps",
                "websocket_connected",
                "live_data_fresh",
                "market_open",
                "ema200_ready",
                "rsi_ready",
                "atr_ready",
                "3m_ready",
                "5m_ready",
                "15m_ready",
            ):
                print(f"    {flag:28} {readiness['flags'].get(flag)}")
            for blocker in readiness["blockers"]:
                print("    blocker:", blocker)
    finally:
        db.close()


if __name__ == "__main__":
    main()
