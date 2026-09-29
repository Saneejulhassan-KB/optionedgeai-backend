"""
Market feed hub — one Upstox V3 connection per user, fan-out to Flutter sockets.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.config import get_settings
from app.core.instruments import PRIMARY_QUOTE_KEYS
from app.core.market_session import is_within_session
from app.core.timeframes import parse_timeframe_list
from app.websocket.feed_state import get_feed_health
from app.websocket.upstox_feed import UpstoxMarketFeedV3, normalize_upstox_mode

logger = logging.getLogger(__name__)


def live_timeframes() -> list[str]:
    return [t.id for t in parse_timeframe_list(get_settings().live_timeframes)]


@dataclass
class FlutterClient:
    websocket: WebSocket
    keys: set[str] = field(default_factory=set)
    mode: str = "ltpc"


@dataclass
class UserFeedSession:
    user_id: int
    access_token: str
    feed: UpstoxMarketFeedV3
    clients: dict[int, FlutterClient] = field(default_factory=dict)
    _next_client_id: int = 0

    def add_client(self, websocket: WebSocket) -> int:
        self._next_client_id += 1
        cid = self._next_client_id
        self.clients[cid] = FlutterClient(websocket=websocket)
        return cid

    def remove_client(self, client_id: int) -> None:
        self.clients.pop(client_id, None)

    def union_keys_and_mode(self) -> tuple[list[str], str]:
        keys: set[str] = set()
        mode = "ltpc"
        for client in self.clients.values():
            keys |= client.keys
            if client.mode in {"full", "option_greeks", "full_d30"}:
                mode = client.mode
        return sorted(keys), mode


class MarketFeedHub:
    """Process-local hub (fine for single uvicorn worker / local Phase 6)."""

    def __init__(self) -> None:
        self._sessions: dict[int, UserFeedSession] = {}
        self._lock = asyncio.Lock()
        self._last_persist: dict[str, float] = {}

    async def attach(
        self,
        *,
        user_id: int,
        access_token: str,
        websocket: WebSocket,
    ) -> tuple[UserFeedSession, int]:
        start_feed: UpstoxMarketFeedV3 | None = None
        async with self._lock:
            session = self._sessions.get(user_id)
            if session is None:
                feed = UpstoxMarketFeedV3(
                    access_token,
                    on_quotes=lambda quotes, uid=user_id: self._on_quotes(uid, quotes),
                    on_reconnected=lambda uid=user_id, tok=access_token: self._backfill_on_reconnect(
                        uid, tok
                    ),
                    health=get_feed_health(user_id),
                )
                session = UserFeedSession(
                    user_id=user_id,
                    access_token=access_token,
                    feed=feed,
                )
                self._sessions[user_id] = session
                start_feed = feed
            else:
                session.access_token = access_token
            client_id = session.add_client(websocket)

        if start_feed is not None:
            await start_feed.start()
        return session, client_id

    async def detach(self, user_id: int, client_id: int) -> None:
        stop_feed: UpstoxMarketFeedV3 | None = None
        resub: tuple[UpstoxMarketFeedV3, list[str], str] | None = None
        async with self._lock:
            session = self._sessions.get(user_id)
            if session is None:
                return
            session.remove_client(client_id)
            if session.clients:
                keys, mode = session.union_keys_and_mode()
                resub = (session.feed, keys, mode)
            else:
                stop_feed = session.feed
                self._sessions.pop(user_id, None)

        if resub is not None:
            feed, keys, mode = resub
            await feed.set_subscriptions(keys, mode)
        if stop_feed is not None:
            await stop_feed.stop()

    async def update_subscription(
        self,
        user_id: int,
        client_id: int,
        keys: list[str],
        mode: str,
        *,
        allow_empty: bool = False,
    ) -> list[str]:
        feed: UpstoxMarketFeedV3 | None = None
        union_keys: list[str] = []
        union_mode = "ltpc"
        cleaned: list[str] = []
        async with self._lock:
            session = self._sessions.get(user_id)
            if session is None:
                return []
            client = session.clients.get(client_id)
            if client is None:
                return []
            cleaned = [k.strip() for k in keys if k and str(k).strip()]
            if not cleaned and not allow_empty:
                cleaned = list(PRIMARY_QUOTE_KEYS)
            client.keys = set(cleaned)
            client.mode = normalize_upstox_mode(mode)
            union_keys, union_mode = session.union_keys_and_mode()
            feed = session.feed

        if feed is not None:
            await feed.set_subscriptions(union_keys, union_mode)
        return cleaned

    async def _backfill_on_reconnect(self, user_id: int, access_token: str) -> None:
        """After Upstox WS reconnect, repair every configured intraday timeframe."""
        from app.database.session import SessionLocal
        from app.services.reconnect_backfill import backfill_after_reconnect

        session = self._sessions.get(user_id)
        keys: list[str] = []
        if session is not None:
            keys, _ = session.union_keys_and_mode()
        if not keys:
            keys = list(PRIMARY_QUOTE_KEYS)

        db = SessionLocal()
        try:
            for key in keys[:8]:  # bound work on reconnect
                try:
                    await backfill_after_reconnect(
                        db, access_token, instrument_key=key
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "websocket_backfill_failed key=%s err=%s", key, exc
                    )
        finally:
            db.close()

    async def _on_quotes(self, user_id: int, quotes: list[dict[str, Any]]) -> None:
        """Fan out to Flutter clients and fold ticks into canonical candles."""
        await self._broadcast(user_id, quotes)
        await self._persist_ticks(quotes)

    async def _persist_ticks(self, quotes: list[dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc)
        if not is_within_session(now):
            # Never manufacture candles outside a real trading session.
            return

        settings = get_settings()
        interval = float(settings.live_candle_persist_interval_seconds)
        timeframes = live_timeframes()
        monotonic = time.monotonic()

        due: list[tuple[str, float]] = []
        for quote in quotes:
            key = str(quote.get("instrument_key") or "")
            ltp = float(quote.get("ltp") or 0)
            if not key or ltp <= 0:
                continue
            last = self._last_persist.get(key)
            if last is not None and (monotonic - last) < interval:
                continue
            self._last_persist[key] = monotonic
            due.append((key, ltp))

        if not due:
            return

        try:
            await asyncio.to_thread(_write_ticks, due, timeframes, now)
        except Exception as exc:  # noqa: BLE001 — never kill the feed on a DB hiccup
            logger.warning("candle_tick_persist_failed err=%s", exc)

    async def _broadcast(self, user_id: int, quotes: list[dict[str, Any]]) -> None:
        session = self._sessions.get(user_id)
        if session is None or not quotes:
            return

        dead: list[int] = []
        for client_id, client in list(session.clients.items()):
            if client.websocket.client_state != WebSocketState.CONNECTED:
                dead.append(client_id)
                continue
            wanted = client.keys
            if not wanted:
                continue
            filtered = [
                q
                for q in quotes
                if q.get("instrument_key") in wanted
                or _alt_key(str(q.get("instrument_key") or "")) in wanted
            ]
            if not filtered:
                continue
            try:
                await client.websocket.send_json({"quotes": filtered})
            except Exception:  # noqa: BLE001
                dead.append(client_id)

        for client_id in dead:
            await self.detach(user_id, client_id)


def _write_ticks(
    ticks: list[tuple[str, float]],
    timeframes: list[str],
    now: datetime,
) -> None:
    """Runs off the event loop: SQLite writes are synchronous."""
    from app.database.session import SessionLocal
    from app.services.candle_reconciler import apply_tick

    db = SessionLocal()
    try:
        for instrument_key, ltp in ticks:
            apply_tick(
                db,
                instrument_key=instrument_key,
                timeframes=timeframes,
                ltp=ltp,
                ts=now,
            )
    finally:
        db.close()


def _alt_key(key: str) -> str:
    return key.replace("|", ":") if "|" in key else key.replace(":", "|")


market_feed_hub = MarketFeedHub()
