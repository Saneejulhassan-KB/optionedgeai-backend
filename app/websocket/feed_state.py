"""
Real upstream WebSocket state.

Readiness must never assume the feed is connected: this registry records what
the Upstox feed task actually observed, and bootstrap reads it verbatim.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

DISCONNECTED = "DISCONNECTED"
CONNECTING = "CONNECTING"
CONNECTED = "CONNECTED"
RECONNECTING = "RECONNECTING"
DEGRADED = "DEGRADED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FeedHealth:
    """Observed health of one upstream Upstox feed connection."""

    status: str = DISCONNECTED
    last_message_at: datetime | None = None
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_successful_reconnect: datetime | None = None
    reconnect_count: int = 0
    disconnect_count: int = 0
    subscribed_keys: int = 0
    last_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def mark_connecting(self, *, reconnect: bool) -> None:
        with self._lock:
            self.status = RECONNECTING if reconnect else CONNECTING

    def mark_connected(self, *, reconnect: bool) -> None:
        with self._lock:
            self.status = CONNECTED
            self.last_connected_at = _now()
            self.last_error = None
            if reconnect:
                self.reconnect_count += 1
                self.last_successful_reconnect = self.last_connected_at

    def mark_disconnected(self, error: str | None = None) -> None:
        with self._lock:
            self.status = DISCONNECTED
            self.last_disconnected_at = _now()
            self.disconnect_count += 1
            if error:
                self.last_error = error[:256]

    def mark_message(self) -> None:
        with self._lock:
            self.last_message_at = _now()

    def set_subscription_count(self, count: int) -> None:
        with self._lock:
            self.subscribed_keys = count

    def staleness_seconds(self, *, now: datetime | None = None) -> float | None:
        if self.last_message_at is None:
            return None
        return ((now or _now()) - self.last_message_at).total_seconds()

    def snapshot(self, *, stale_after_seconds: float, now: datetime | None = None) -> dict[str, Any]:
        """Effective state including staleness — the single truth for readiness."""
        staleness = self.staleness_seconds(now=now)
        status = self.status
        is_connected = status == CONNECTED

        if is_connected and staleness is not None and staleness > stale_after_seconds:
            status = DEGRADED

        data_fresh = (
            is_connected and staleness is not None and staleness <= stale_after_seconds
        )
        return {
            "status": status,
            "connected": is_connected,
            "data_fresh": data_fresh,
            "staleness_seconds": staleness,
            "stale_after_seconds": stale_after_seconds,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "last_connected_at": (
                self.last_connected_at.isoformat() if self.last_connected_at else None
            ),
            "last_successful_reconnect": (
                self.last_successful_reconnect.isoformat()
                if self.last_successful_reconnect
                else None
            ),
            "reconnect_count": self.reconnect_count,
            "disconnect_count": self.disconnect_count,
            "subscribed_keys": self.subscribed_keys,
            "last_error": self.last_error,
        }


_registry: dict[int, FeedHealth] = {}
_registry_lock = threading.Lock()


def get_feed_health(user_id: int) -> FeedHealth:
    """Health record for a user's feed. Absent feed reads as DISCONNECTED."""
    with _registry_lock:
        health = _registry.get(user_id)
        if health is None:
            health = FeedHealth()
            _registry[user_id] = health
        return health
