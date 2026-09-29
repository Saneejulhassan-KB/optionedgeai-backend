"""
Flutter ↔ Backend market WebSocket.

Endpoint:  WS /ws/market?token=<backend_jwt>

Protocol (Flutter BackendMarketSocket):
  → { "action": "subscribe", "keys": [...], "mode": "ltpc" }
  → { "action": "unsubscribe" }  (optional)
  → { "action": "ping" }
  ← { "quotes": [ { instrument_key, ltp, ... }, ... ] }
  ← { "type": "ready" | "subscribed" | "error" | "pong", ... }
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.auth.jwt import JwtError, user_id_from_token
from app.core.instruments import PRIMARY_QUOTE_KEYS
from app.database.session import SessionLocal
from app.repositories import auth_repository
from app.websocket.hub import market_feed_hub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Market WebSocket"])


def _resolve_user_and_upstox_token(token: str) -> tuple[int, str]:
    """Validate backend JWT and load a non-expired Upstox access_token."""
    user_id = user_id_from_token(token)
    db: Session = SessionLocal()
    try:
        user = auth_repository.get_user_by_id(db, user_id)
        if user is None:
            raise JwtError("User not found.")

        token_row = auth_repository.get_oauth_token_for_user(db, user.id)
        if token_row is None or not token_row.access_token:
            raise JwtError("No Upstox session. Please login again.")

        expires_at = token_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(tz=timezone.utc):
            raise JwtError("Upstox access token expired. Please login again.")

        return user.id, token_row.access_token
    finally:
        db.close()


@router.websocket("/ws/market")
async def market_websocket(websocket: WebSocket) -> None:
    """
    Authenticate via ?token=<jwt>, then fan-out Upstox V3 ticks as JSON.
    """
    token = websocket.query_params.get("token") or ""
    if not token:
        # Also accept first Authorization-style header if present
        auth = websocket.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        user_id, upstox_token = _resolve_user_and_upstox_token(token)
    except JwtError as exc:
        logger.info("WS auth failed: %s", exc.message)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    session, client_id = await market_feed_hub.attach(
        user_id=user_id,
        access_token=upstox_token,
        websocket=websocket,
    )

    await websocket.send_json(
        {
            "type": "ready",
            "message": "Connected. Send subscribe with keys + mode.",
            "default_keys": list(PRIMARY_QUOTE_KEYS),
        }
    )

    try:
        while True:
            raw = await websocket.receive_json()
            if not isinstance(raw, dict):
                continue
            await _handle_client_message(
                websocket,
                user_id=user_id,
                client_id=client_id,
                message=raw,
            )
    except WebSocketDisconnect:
        logger.debug("Flutter WS disconnected user=%s client=%s", user_id, client_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Flutter WS error user=%s: %s", user_id, exc)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        await market_feed_hub.detach(user_id, client_id)


async def _handle_client_message(
    websocket: WebSocket,
    *,
    user_id: int,
    client_id: int,
    message: dict[str, Any],
) -> None:
    action = str(message.get("action") or message.get("method") or "").lower()

    if action in {"ping", "heartbeat"}:
        await websocket.send_json({"type": "pong"})
        return

    if action in {"unsubscribe", "unsub"}:
        await market_feed_hub.update_subscription(
            user_id, client_id, [], "ltpc", allow_empty=True
        )
        await websocket.send_json({"type": "unsubscribed"})
        return

    if action in {"subscribe", "sub", ""}:
        keys_raw = message.get("keys") or message.get("instrumentKeys") or []
        if isinstance(keys_raw, str):
            keys = [part.strip() for part in keys_raw.split(",") if part.strip()]
        elif isinstance(keys_raw, list):
            keys = [str(k).strip() for k in keys_raw if str(k).strip()]
        else:
            keys = list(PRIMARY_QUOTE_KEYS)

        mode = str(message.get("mode") or "ltpc")
        applied = await market_feed_hub.update_subscription(
            user_id, client_id, keys, mode
        )
        await websocket.send_json(
            {
                "type": "subscribed",
                "keys": applied,
                "mode": mode,
            }
        )
        return

    await websocket.send_json(
        {
            "type": "error",
            "message": f"Unknown action '{action}'. Use subscribe / unsubscribe / ping.",
        }
    )
