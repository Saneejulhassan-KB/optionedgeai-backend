# Phase 5.4 — Candles (OHLCV)

## Purpose

Historical + current-day candles for trend / VWAP / intraday engines:

```
Flutter (JWT)
  → GET /market/candles?key=...&interval=5minute&from=YYYY-MM-DD&to=YYYY-MM-DD
  → Backend → Upstox V3 historical (+ intraday if range includes today IST)
  → { instrument_key, interval, from_date, to_date, candles: [ { timestamp, o,h,l,c, volume } ] }
```

---

## Endpoint

`GET /market/candles`

| Query | Default | Notes |
|-------|---------|-------|
| `key` | `NSE_INDEX\|Nifty 50` | Single instrument key |
| `interval` | `5minute` | See aliases below |
| `from` | `to − 2 days` | `YYYY-MM-DD` |
| `to` | today (IST) | `YYYY-MM-DD` |

**Auth:** Bearer JWT

### Interval aliases

| Flutter / query | Upstox V3 |
|-----------------|-----------|
| `1minute`, `5minute`, `15minute`, `30minute` | `minutes/N` |
| `day`, `daily` | `days/1` |
| `week` | `weeks/1` |
| `month` | `months/1` |
| `minutes/5` | passed through |

### Merge rule

If `to` is today (Asia/Kolkata):

1. Historical candles for `from` → yesterday  
2. Intraday candles for today  
3. Dedupe by timestamp, sort ascending

---

## Example response

```json
{
  "instrument_key": "NSE_INDEX|Nifty 50",
  "interval": "minutes/5",
  "from_date": "2026-07-22",
  "to_date": "2026-07-24",
  "candles": [
    {
      "timestamp": "2026-07-22T09:15:00+05:30",
      "open": 25010.0,
      "high": 25025.5,
      "low": 25005.0,
      "close": 25020.0,
      "volume": 0
    }
  ]
}
```

---

## Flutter

`MarketRepository.getCandles(...)` uses this API when JWT session is active  
(Home snapshots + Intraday tab: `5minute` / `day`).

---

## How to test

1. Backend on **8040**, JWT login OK  
2. Docs → `GET /market/candles` with defaults  
3. Or: `interval=day&from=2026-07-01&to=2026-07-24`  
4. Hot restart app → Home / Intraday should use live candles  

---

## Next

**Phase 6 — WebSocket V3** is next (`docs/phase6_websocket_v3.md`), then orders/positions.
