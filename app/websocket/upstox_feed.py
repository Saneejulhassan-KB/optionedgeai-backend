"""
Outbound Upstox Market Data Feed V3 WebSocket client.

Flow:
  1. GET /v3/feed/market-data-feed/authorize → authorized_redirect_uri
  2. Connect to that wss URL
  3. Send JSON subscribe { guid, method: sub, data: { mode, instrumentKeys } }
  4. Receive binary protobuf → decode → quote dicts → callback
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

from app.config import get_settings
from app.websocket.feed_decoder import decode_feed_response, quotes_from_feed_dict
from app.websocket.feed_state import FeedHealth

logger = logging.getLogger(__name__)

QuoteHandler = Callable[[list[dict[str, Any]]], Awaitable[None]]
ReconnectHandler = Callable[[], Awaitable[None]]

_VALID_MODES = {"ltpc", "full", "option_greeks", "full_d30"}


def normalize_upstox_mode(mode: str | None) -> str:
    raw = (mode or "ltpc").strip().lower()
    if raw in {"greeks", "option", "option_greek"}:
        return "option_greeks"
    if raw in {"full_d5", "full5"}:
        return "full"
    if raw not in _VALID_MODES:
        return "ltpc"
    return raw


class UpstoxMarketFeedV3:
    """One upstream connection to Upstox V3 for a single access_token."""

    def __init__(
        self,
        access_token: str,
        on_quotes: QuoteHandler,
        on_reconnected: ReconnectHandler | None = None,
        health: FeedHealth | None = None,
    ) -> None:
        self._access_token = access_token
        self._on_quotes = on_quotes
        self._on_reconnected = on_reconnected
        self._health = health or FeedHealth()
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._subscribed: set[str] = set()
        self._mode: str = "ltpc"
        self._lock = asyncio.Lock()
        self._ever_connected = False

    @property
    def health(self) -> FeedHealth:
        return self._health

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="upstox-feed-v3")

    async def stop(self) -> None:
        self._stop.set()
        self._health.mark_disconnected("feed stopped")
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    async def set_subscriptions(self, keys: list[str], mode: str = "ltpc") -> None:
        cleaned = [k.strip() for k in keys if k and k.strip()]
        mode_n = normalize_upstox_mode(mode)
        async with self._lock:
            new_set = set(cleaned)
            mode_changed = mode_n != self._mode
            to_add = new_set - self._subscribed
            to_remove = self._subscribed - new_set
            self._mode = mode_n
            self._subscribed = new_set
        self._health.set_subscription_count(len(new_set))

        if self._ws is None:
            return

        if to_remove:
            await self._send_method("unsub", list(to_remove), mode_n)
        if to_add or (mode_changed and new_set):
            # change_mode if only mode changed with same keys
            if mode_changed and not to_add and not to_remove and new_set:
                await self._send_method("change_mode", list(new_set), mode_n)
            elif to_add or new_set:
                await self._send_method("sub", list(to_add or new_set), mode_n)

    async def _run_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            is_reconnect = self._ever_connected
            try:
                self._health.mark_connecting(reconnect=is_reconnect)
                logger.info(
                    "websocket_%s", "reconnecting" if is_reconnect else "connecting"
                )
                uri = await self._authorize()
                async with websockets.connect(
                    uri,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=8 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    backoff = 1.0
                    self._health.mark_connected(reconnect=is_reconnect)
                    logger.info(
                        "websocket_connected mode=%s keys=%s reconnect=%s",
                        self._mode,
                        len(self._subscribed),
                        is_reconnect,
                    )
                    if self._subscribed:
                        await self._send_method("sub", list(self._subscribed), self._mode)
                    if is_reconnect and self._on_reconnected is not None:
                        try:
                            await self._on_reconnected()
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("websocket_reconnect_backfill_failed: %s", exc)
                    self._ever_connected = True
                    await self._read_forever(ws)
                self._health.mark_disconnected()
                logger.info("websocket_disconnected reason=stream_closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._health.mark_disconnected(str(exc))
                logger.warning("websocket_disconnected error=%s", exc)
                self._ws = None
                if self._stop.is_set():
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
        self._ws = None
        self._health.mark_disconnected("feed loop ended")

    async def _authorize(self) -> str:
        settings = get_settings()
        url = f"{settings.upstox_base_url.rstrip('/')}/v3/feed/market-data-feed/authorize"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Upstox feed authorize failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        data = payload.get("data") or {}
        uri = data.get("authorized_redirect_uri")
        if not uri:
            raise RuntimeError("Upstox feed authorize response missing URI.")
        return str(uri)

    async def _send_method(self, method: str, keys: list[str], mode: str) -> None:
        if self._ws is None or not keys:
            return
        body = {
            "guid": uuid.uuid4().hex[:20],
            "method": method,
            "data": {
                "mode": mode if mode != "full" else "full",
                "instrumentKeys": keys,
            },
        }
        await self._ws.send(json.dumps(body).encode("utf-8"))

    async def _read_forever(self, ws: ClientConnection) -> None:
        async for message in ws:
            if self._stop.is_set():
                break
            if isinstance(message, str):
                # occasional text control frames — ignore
                continue
            if not isinstance(message, bytes):
                continue
            try:
                decoded = decode_feed_response(message)
                self._health.mark_message()
                quotes = quotes_from_feed_dict(decoded)
                if quotes:
                    await self._on_quotes(quotes)
                elif decoded.get("type") is not None:
                    logger.debug("Upstox frame type=%s feeds=%s", decoded.get("type"), list((decoded.get("feeds") or {}).keys())[:4])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to decode Upstox feed frame (%s bytes): %s", len(message), exc)
