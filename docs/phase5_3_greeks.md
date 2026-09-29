# Phase 5.3 — Option Greeks

## Purpose

Dedicated Greeks refresh for Strike Analyzer / Greeks Dashboard:

```
Flutter (JWT)
  → GET /market/greeks?keys=NSE_FO|...,NSE_FO|...
  → Backend → Upstox V3 /market-quote/option-greek
  → { greeks: [ { instrument_key, delta, gamma, theta, vega, iv, ltp, oi, ... } ] }
```

---

## Endpoint

`GET /market/greeks`

| Query | Default | Notes |
|-------|---------|-------|
| `keys` | (optional) | Comma-separated FO keys, max 50. If omitted → NIFTY ATM CE+PE |

**Auth:** Bearer JWT

**Example response:**

```json
{
  "greeks": [
    {
      "instrument_key": "NSE_FO|12345",
      "ltp": 120.5,
      "volume": 10000,
      "oi": 250000,
      "close": 115.0,
      "delta": 0.52,
      "gamma": 0.0012,
      "theta": -8.4,
      "vega": 6.1,
      "rho": 0.0,
      "iv": 14.2
    }
  ]
}
```

`iv` is returned as **percent** (e.g. 14.2), even if Upstox V3 sends a decimal.

---

## Flutter

`MarketRepository.getGreeks(instrumentKey)` uses this API when JWT SESSION is active.

---

## How to test

1. Backend on **8040** (or current port)
2. JWT login OK
3. Docs → `GET /market/greeks` (no keys = ATM defaults)
4. Or pass ATM keys from option-chain CE/PE

---

## Next

**Phase 5.4 — Candles** (`GET /market/candles`) then **Phase 6 WebSocket V3**.
