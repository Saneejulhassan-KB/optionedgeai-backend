# Phase 5.2 — Option Chain

## Purpose

Serve a Flutter-ready option chain from Upstox:

```
Flutter (JWT)
  → GET /market/option-chain?underlying=NSE_INDEX|Nifty 50&expiry=current_week
  → Backend uses stored Upstox token
  → Upstox GET /v2/option/chain
  → Normalized strikes (CE/PE, OI, IV, Greeks, ATM, PCR)
```

Also:

`GET /market/option-expiries?underlying=...` — expiry picker list.

---

## Endpoints

### Option chain

`GET /market/option-chain`

| Query | Default | Notes |
|-------|---------|-------|
| `underlying` | `NSE_INDEX\|Nifty 50` | Instrument key |
| `expiry` | `current_week` | `YYYY-MM-DD` or `current_week` / `next_week` / … |

**Auth:** Bearer JWT

**Response (shape):**

```json
{
  "underlying": "NSE_INDEX|Nifty 50",
  "spot": 23711.0,
  "expiry": "2026-07-31",
  "atm_strike": 23700,
  "pcr": 0.92,
  "strikes": [
    {
      "strike": 23700,
      "moneyness": "atm",
      "is_atm": true,
      "ce": { "instrument_key": "NSE_FO|...", "ltp": 120.5, "oi": 100000, "iv": 12.5, "greeks": { "delta": 0.5, "...": "..." } },
      "pe": { "instrument_key": "NSE_FO|...", "ltp": 95.0, "oi": 90000, "iv": 13.1, "greeks": { "...": "..." } }
    }
  ]
}
```

### Expiries

`GET /market/option-expiries?underlying=NSE_INDEX|Nifty 50`

```json
{ "underlying": "NSE_INDEX|Nifty 50", "expiries": ["2026-07-31", "2026-08-07"] }
```

---

## Flutter

When **JWT SESSION** is active, Chain screen uses live `/market/option-chain`.
Base URL must be `http://10.0.2.2:8030` (emulator).

---

## How to test

1. Backend on **8040**, JWT login OK  
2. App Settings base URL: `http://10.0.2.2:8040` → Save & test  
3. http://127.0.0.1:8040/docs → Authorize → `GET /market/option-chain`  
4. Hot restart app → open **Chain** tab  

---

## Next

**Phase 5.4 — Candles** (`GET /market/candles`) then **Phase 6 WebSocket V3**.
