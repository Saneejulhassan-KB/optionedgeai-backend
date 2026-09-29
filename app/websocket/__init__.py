"""WebSocket package — Upstox Market Data Feed V3 → Flutter JSON fan-out."""

from app.websocket.market_ws import router as market_ws_router

__all__ = ["market_ws_router"]
