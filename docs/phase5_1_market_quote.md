# Phase 5.1 — Market Quote

## Purpose

Let Flutter load live LTPs for **India VIX + NIFTY + BANK NIFTY + SENSEX**
without talking to Upstox directly.

```
Flutter (JWT)
   → GET /market/quote?keys=...
   → Backend loads Upstox access_token from DB
   → Upstox GET /v2/market-quote/quotes
   → Normalized JSON back to Flutter
```

---

## Endpoint

### `GET /market/quote`

**Auth:** `Authorization: Bearer <backend-jwt>`

**Query:**
- `keys` (optional) — comma-separated instrument keys  
  If omitted → VIX + NIFTY + BANK NIFTY + SENSEX

**Example request:**

```http
GET /market/quote?keys=NSE_INDEX|Nifty%2050,NSE_INDEX|India%20VIX
Authorization: Bearer eyJhbGciOi...
```

**Example response:**

```json
{
  "quotes": [
    {
      "instrument_key": "NSE_INDEX|Nifty 50",
      "symbol": "Nifty 50",
      "ltp": 24850.5,
      "open": 24790.0,
      "high": 24910.0,
      "low": 24750.0,
      "close": 24780.0,
      "volume": 0,
      "change": 70.5,
      "change_percent": 0.28,
      "timestamp": "2026-07-24T11:30:00+05:30"
    }
  ]
}
```

**Errors:**
- `401` — missing/expired JWT or Upstox session (login again)
- `502` — Upstox API failure

### `GET /market/quote/defaults`

Lists default keys (no auth). Useful for docs/debugging.

---

## Flutter

When JWT session is active, `MarketRepository` uses backend quotes.
Chain / candles / WS stay on mock until later Phase 5–6 steps.

---

## How to test

1. Backend running on `:8030`
2. App logged in (**JWT SESSION**)
3. In Settings set Base URL to `http://10.0.2.2:8030` (emulator) → Save & test
4. Swagger → http://127.0.0.1:8030/docs → Authorize with JWT → `GET /market/quote`
5. Hot restart app — Home index LTPs should be live when logged in

Outside market hours you still get the last available snapshot from Upstox.

---

## Next (Phase 5.2)

`GET /market/option-chain` for NIFTY first.
