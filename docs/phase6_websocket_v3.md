# Phase 6 — WebSocket V3 fan-out

## Purpose

Flutter never opens an Upstox WebSocket. Backend owns one Upstox Market Data Feed V3 connection per logged-in user, decodes protobuf, and fans out JSON ticks to Flutter.

```
Flutter
  ws://host:8040/ws/market?token=<backend_jwt>
  → { "action": "subscribe", "keys": [...], "mode": "ltpc" }
  ← { "quotes": [ { instrument_key, ltp, open, high, low, close, change, ... } ] }

Backend
  → GET /v3/feed/market-data-feed/authorize
  → wss://… Upstox Market Data Feed V3 (protobuf)
  → decode → JSON fan-out
```

---

## Endpoint

`WS /ws/market?token=<JWT>`

| Auth | `token` query (preferred) or `Authorization: Bearer` on handshake |
|------|-------------------------------------------------------------------|
| Required | Valid backend JWT + non-expired Upstox OAuth token on server |

### Client → server

```json
{ "action": "subscribe", "keys": ["NSE_INDEX|Nifty 50", "..."], "mode": "ltpc" }
{ "action": "unsubscribe" }
{ "action": "ping" }
```

Modes: `ltpc` (default), `full`, `option_greeks` / `greeks`, `full_d30`

### Server → client

```json
{ "type": "ready", "default_keys": [...] }
{ "type": "subscribed", "keys": [...], "mode": "ltpc" }
{ "quotes": [ { "instrument_key": "NSE_INDEX|Nifty 50", "ltp": 23784.7, "cp": 23666.35, ... } ] }
{ "type": "pong" }
{ "type": "error", "message": "..." }
```

---

## Architecture notes

| Piece | Role |
|-------|------|
| `app/websocket/upstox_feed.py` | Authorize + Upstox WS + protobuf decode |
| `app/websocket/hub.py` | One Upstox feed per user; fan-out to Flutter sockets |
| `app/websocket/market_ws.py` | FastAPI `/ws/market` |
| `app/websocket/proto/` | Generated `MarketDataFeed_pb2` |

Single-worker only for now (in-memory hub). Multi-worker later can use Redis pub/sub.

---

## Flutter

`BackendMarketSocket` connects with JWT query param.  
`MarketRepository.watchLiveQuotes` uses WS when logged in (REST seed + live ticks). Fallback: REST poll every 3s.

---

## How to test

1. Backend on **8040**, JWT login OK (market hours preferred for live ticks)
2. Python smoke (see below) or Flutter hot-restart → Home live quotes
3. Docs won’t show WS in Swagger the same way — use a WS client

```powershell
# After login, put JWT in TOKEN
.\.venv\Scripts\python.exe -c "..."
```

---

## Next

**Phase 7 — Orders / Positions / Funds** (or expand WS modes for FO options).
