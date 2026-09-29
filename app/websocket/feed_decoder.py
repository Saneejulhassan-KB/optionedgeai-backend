"""
Decode Upstox Market Data Feed V3 protobuf → Flutter-friendly quote dicts.
"""

from __future__ import annotations

from typing import Any

from google.protobuf.json_format import MessageToDict

from app.websocket.proto import MarketDataFeed_pb2 as pb2


def decode_feed_response(buffer: bytes) -> dict[str, Any]:
    """Parse binary protobuf FeedResponse into a plain dict."""
    msg = pb2.FeedResponse()
    msg.ParseFromString(buffer)
    return MessageToDict(msg, preserving_proto_field_name=True)


def quotes_from_feed_dict(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract quote ticks from a decoded FeedResponse dict.

    Upstox keys often use colon (`NSE_INDEX:Nifty 50`); we normalize to pipe
    (`NSE_INDEX|Nifty 50`) to match Flutter / REST instrument_key.
    """
    feeds = data.get("feeds") or {}
    if not isinstance(feeds, dict):
        return []

    out: list[dict[str, Any]] = []
    for raw_key, feed in feeds.items():
        if not isinstance(feed, dict):
            continue
        instrument_key = _normalize_key(str(raw_key))
        tick = _feed_to_quote(instrument_key, feed)
        if tick is not None:
            out.append(tick)
    return out


def _normalize_key(key: str) -> str:
    # Upstox WS aliases often replace | with :
    if "|" not in key and ":" in key:
        parts = key.split(":", 1)
        if len(parts) == 2 and parts[0] in {
            "NSE_INDEX",
            "BSE_INDEX",
            "NSE_FO",
            "BSE_FO",
            "NSE_EQ",
            "BSE_EQ",
            "MCX_FO",
        }:
            return f"{parts[0]}|{parts[1]}"
    return key


def _feed_to_quote(instrument_key: str, feed: dict[str, Any]) -> dict[str, Any] | None:
    ltpc = feed.get("ltpc")
    ohlc_list: list[dict[str, Any]] = []

    if not isinstance(ltpc, dict):
        full = feed.get("fullFeed") or {}
        if isinstance(full, dict):
            market_ff = full.get("marketFF") or full.get("indexFF") or {}
            if isinstance(market_ff, dict):
                ltpc = market_ff.get("ltpc")
                market_ohlc = market_ff.get("marketOHLC") or {}
                if isinstance(market_ohlc, dict):
                    raw = market_ohlc.get("ohlc") or []
                    if isinstance(raw, list):
                        ohlc_list = [x for x in raw if isinstance(x, dict)]

        first = feed.get("firstLevelWithGreeks")
        if not isinstance(ltpc, dict) and isinstance(first, dict):
            ltpc = first.get("ltpc")

    if not isinstance(ltpc, dict):
        return None

    ltp = float(ltpc.get("ltp") or 0)
    cp = float(ltpc.get("cp") or 0)
    if ltp == 0 and cp == 0:
        return None

    open_, high, low, close, volume = _pick_ohlc(ohlc_list, fallback_close=cp or ltp)
    close = close or cp or ltp
    change = ltp - close if close else 0.0

    return {
        "instrument_key": instrument_key,
        "symbol": instrument_key.split("|")[-1] if "|" in instrument_key else instrument_key,
        "ltp": ltp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "cp": cp,
        "volume": volume,
        "change": change,
        "change_percent": (change / close * 100.0) if close else 0.0,
    }


def _pick_ohlc(
    ohlc_list: list[dict[str, Any]],
    *,
    fallback_close: float,
) -> tuple[float, float, float, float, int]:
    """Prefer 1d candle when present; else first row; else LTP-based stubs."""
    preferred = None
    for row in ohlc_list:
        interval = str(row.get("interval") or "").lower()
        if interval in {"1d", "day", "I1"} or interval == "1D":
            preferred = row
            break
    if preferred is None and ohlc_list:
        preferred = ohlc_list[0]

    if preferred is None:
        return fallback_close, fallback_close, fallback_close, fallback_close, 0

    return (
        float(preferred.get("open") or fallback_close),
        float(preferred.get("high") or fallback_close),
        float(preferred.get("low") or fallback_close),
        float(preferred.get("close") or fallback_close),
        int(preferred.get("vol") or 0),
    )
