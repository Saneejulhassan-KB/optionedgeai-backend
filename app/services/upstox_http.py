"""
Shared outbound HTTP to Upstox — one client, capped concurrency.

Flutter can open dozens of candle/quote requests at once. Without a cap,
each opens its own TLS session and starves the event loop → /health hangs
and the app shows OFFLINE.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx

_client: Optional[httpx.AsyncClient] = None
_semaphore: Optional[asyncio.Semaphore] = None
_max_concurrent: int = 6


async def startup_upstox_http(*, max_concurrent: int = 6) -> None:
    """Create the shared client + semaphore (call from app lifespan)."""
    global _client, _semaphore, _max_concurrent
    _max_concurrent = max(1, max_concurrent)
    _semaphore = asyncio.Semaphore(_max_concurrent)
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(
            max_connections=_max_concurrent + 4,
            max_keepalive_connections=_max_concurrent,
        ),
    )


async def shutdown_upstox_http() -> None:
    """Close the shared client on app shutdown."""
    global _client, _semaphore
    if _client is not None:
        await _client.aclose()
    _client = None
    _semaphore = None


def get_upstox_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("Upstox HTTP client not started — check app lifespan.")
    return _client


class upstox_http_slot:
    """Async context manager: wait for a concurrency slot, then yield the client."""

    async def __aenter__(self) -> httpx.AsyncClient:
        if _semaphore is None or _client is None:
            raise RuntimeError("Upstox HTTP client not started — check app lifespan.")
        await _semaphore.acquire()
        return _client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if _semaphore is not None:
            _semaphore.release()
