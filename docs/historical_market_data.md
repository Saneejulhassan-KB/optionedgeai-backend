# Historical Market Data Foundation

## Pipeline

```text
Upstox Historical Candle API V3   (chunked, resumable, chunk ledger)
        │
Upstox Intraday V3 (today's session, closed/forming classified by IST clock)
        │
Live WebSocket ticks
        │
        ▼
  Candle reconciler  ──▶  candles table (instrument_key + timeframe + timestamp UTC)
        │
        ├─▶ coverage verification + hole detection
        ├─▶ indicators (warmup-aware)
        ├─▶ market structure
        ▼
   readiness  ──▶  GET /api/v1/market/bootstrap  ──▶ Flutter
```

## Full history (not "last 10 days")

`FULL HISTORY` = maximum period **Upstox documents as available** for that timeframe:

| Timeframe | Available from (provider) | Max chunk / request |
|-----------|---------------------------|---------------------|
| 1m–15m    | Jan 2022                  | ~28 days            |
| 30m, 1h   | Jan 2022                  | ~90 days            |
| 1D        | Jan 2000                  | ~10 years           |

Configured defaults: instruments NIFTY / BANKNIFTY / SENSEX; timeframes `3m,5m,15m,1D`.

## Coverage lifecycle

`historical_coverage.status` is one of:

| Status | Meaning |
|--------|---------|
| `REQUESTED` | A window was asked for; nothing verified yet |
| `DOWNLOADING` | Chunks are in flight |
| `PARTIAL` | Chunks failed, the earliest date was not reached, or gaps were found |
| `COMPLETE` | Every planned chunk succeeded, candles exist, earliest verified, no gaps |
| `FAILED` | The provider returned nothing for the requested range |

"The table has rows" is never treated as "history is complete". A sync is only
marked `COMPLETE` after `_finalize` verifies the stored data.

### Chunk ledger

Chunk windows are anchored on the provider start date, so the same window key is
produced on every run. `historical_chunks` records each
`(instrument_key, timeframe, from_date, to_date)` as `COMPLETED` or `FAILED`:

- completed chunk → skipped on the next run
- failed chunk → retried
- never-attempted chunk → downloaded

A failed chunk no longer aborts the run; the remaining range is still fetched and
the series stays `PARTIAL`.

### Hole detection

Trading days come from the instrument's **own 1D candle series**, so weekends and
exchange holidays are never mistaken for missing data and no holiday calendar is
invented. For each real trading day, the expected candle opens (09:15–15:30 IST,
stepped by the timeframe) are compared with what is stored; contiguous missing runs
are reported as `SUSPICIOUS_GAP`. If no 1D series exists the report is marked
`verifiable: false` rather than guessing. The scan is bounded by
`COVERAGE_GAP_SCAN_DAYS`, and the historical pass stops at yesterday's close so
today's in-progress session is not counted against it.

## Candle lifecycle

```text
FORMING ──(interval elapses)──▶ CLOSED   (a closed candle never reopens)
```

Boundaries are anchored on the session open, so a 3m candle is 09:15–09:18, never
09:16–09:19. A session loaded at 11:00 stores 09:15…10:55 as CLOSED and only
11:00–11:05 as FORMING. `finalize_due_candles` also runs on read paths, so a candle
cannot stay open just because no tick arrived after its boundary.

### Source precedence

| Rank | Source | Notes |
|------|--------|-------|
| 3 | `upstox_historical` | Most authoritative |
| 2 | `upstox_intraday` | Corrects live-built candles |
| 1 | `websocket` | Only fills the currently forming candle |

Enforced in `CandleRepository.bulk_upsert`: a lower-ranked source may only write
when the stored candle is still forming, and `is_closed` is sticky. Live ticks
update OHLC only — volume and OI come from the provider candle APIs, because the
feed reports cumulative day volume rather than a per-candle value.

## WebSocket

Real state is tracked per user in `app/websocket/feed_state.py`:
`DISCONNECTED → CONNECTING → CONNECTED → RECONNECTING`, plus `DEGRADED` when the
connection is up but no message has arrived within `DATA_STALE_THRESHOLD_SECONDS`.
Bootstrap reports this verbatim; it never assumes `connected: true`.

On reconnect, every timeframe in `RECONNECT_BACKFILL_TIMEFRAMES` (default
`3m,5m,15m`) is repaired. The window starts at the last stored **closed** candle:
if the disconnect spanned earlier sessions the historical endpoint fills those days,
then the intraday endpoint refreshes today. Overlapping requests are safe because
the upsert is idempotent.

## Readiness

`DATA_AVAILABLE` and `TRADING_READY` are separate. `can_trade` is true only when
all of these hold for every timeframe in `TRADING_TIMEFRAMES`:

`historical_coverage_ready`, `today_session_ready`, `required_indicators_ready`,
`market_structure_ready`, `no_critical_data_gaps`, `websocket_connected`,
`live_data_fresh`, `market_open`.

Per-indicator flags (`ema9_ready` … `ema200_ready`, `rsi_ready`, `atr_ready`,
`vwap_ready`, `structure_ready`) are exposed both globally and namespaced per
timeframe (`5m_ema200_ready`). An indicator is `READY` only with enough warmup:
`period * INDICATOR_WARMUP_MULTIPLIER` bars, so EMA200 needs 400 bars by default,
not the 200 that merely make the maths possible. VWAP is informational because
index feeds carry no volume. `blockers` lists exactly why trading is disabled.

## Timezone rules

- Persistence is always timezone-aware **UTC** (`UtcDateTime` column type).
- Asia/Kolkata is used only for session boundaries, candle bucketing and display.
- Naive datetimes reaching the database are treated as UTC; naive timestamps from
  Upstox are exchange-local and parsed as IST.

## Endpoints (legacy `/market/*` unchanged)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/market/sync` | JWT+Upstox | Resume full history + today |
| GET | `/api/v1/market/sync/status` | JWT | Progress of the last sync |
| GET | `/api/v1/market/verify` | none* | Evidence: requested vs. actual coverage |
| GET | `/api/v1/market/bootstrap` | optional JWT | Coverage + readiness + state |
| GET | `/api/v1/market/candles` | none* | Read local store |
| GET | `/api/v1/market/state` | optional JWT | One MarketState |
| GET | `/api/v1/market/coverage` | none* | Coverage rows |

\*Read endpoints hit the local DB only (no Upstox secrets). Bootstrap and state
report per-user feed health when a JWT is supplied; without one the feed is
reported as unknown/disconnected rather than assumed healthy.

## Flutter startup

```text
Login (JWT)
  → POST /api/v1/market/sync
  → poll GET /api/v1/market/bootstrap until status READY
  → connect WS /ws/market
  → enable trading UI only when readiness.can_trade == true
```

## Verification

`python -m scripts.verify_live_sync [--days N] [--instrument KEY] [--timeframe TF]`
runs an authenticated sync and prints requested vs. actual coverage per series.
It never prints tokens.

## Config (`.env`)

See `HISTORICAL_*`, `LIVE_TIMEFRAMES`, `RECONNECT_BACKFILL_TIMEFRAMES`,
`TRADING_TIMEFRAMES`, `INDICATOR_WARMUP_MULTIPLIER`, `ACTIVE_CANDLE_LOOKBACK`,
`COVERAGE_GAP_SCAN_DAYS` and `DATA_STALE_THRESHOLD_SECONDS` in `.env.example`.
