# OptionEdgeAI Backend — What Actually Happens Here

This document explains the **whole backend** in plain language: why it exists, how Flutter talks to it, how Upstox stays secret, and what each part does.

For phase-by-phase deep dives, see the other files in `docs/`.

---

## 1. One-sentence purpose

**Flutter never talks to Upstox.** The app talks only to this FastAPI server. The server holds Upstox secrets and tokens, calls Upstox on behalf of the user, and returns clean JSON (or WebSocket ticks) to Flutter.

```
┌─────────────┐         JWT + JSON/WS          ┌──────────────────┐         API key +
│  Flutter    │  ←──────────────────────────→  │  This backend    │  ←───→  │ Upstox
│  OptionEdge │    http://10.0.2.2:8040        │  FastAPI         │         │ REST + WS
└─────────────┘                                └──────────────────┘         └────────
     │                                                  │
     │  never sees                                      │  stores in SQLite
     │  UPSTOX_API_SECRET                               │  User + OAuthToken
     │  Upstox access_token                             │  (access / refresh)
```

---

## 2. Who talks to whom

| Actor | Talks to | Does not talk to |
|-------|----------|------------------|
| Flutter app | Backend only (`/auth`, `/market`, `/ws/market`, `/health`) | Upstox directly |
| Backend | Flutter + Upstox + local SQLite | — |
| Upstox | Backend (OAuth, quotes, chain, candles, market feed) | Flutter |

**Emulator networking:** Android emulator reaches your PC at `10.0.2.2`, not `127.0.0.1`. So Flutter Base URL is usually:

```text
http://10.0.2.2:8040
```

**OAuth redirect:** Upstox requires an **HTTPS** callback URL. Localhost HTTP is not enough for real device / TPIN flow, so we expose the backend with a Cloudflare quick tunnel (`*.trycloudflare.com`) and register that as `UPSTOX_REDIRECT_URI`.

---

## 3. Big picture: request life cycle

### A) First-time login (OAuth)

```
1. Flutter  →  GET /auth/login
2. Backend  →  builds Upstox authorization URL (client_id + redirect_uri + state)
3. Flutter  →  opens that URL in browser / WebView
4. User     →  logs in at Upstox (TPIN / consent)
5. Upstox   →  redirects browser to https://<tunnel>/auth/callback?code=...&state=...
6. Backend  →  exchanges code for Upstox access_token (using API secret)
7. Backend  →  saves User + OAuthToken in SQLite; marks login session completed
8. Flutter  →  polls GET /auth/login/status?state=...
9. Backend  →  returns a **backend JWT** (not the Upstox token) + public profile
10. Flutter →  stores JWT; sends `Authorization: Bearer <jwt>` on later calls
```

**Important:** Flutter never receives the Upstox access token. It only gets our JWT.

### B) Normal market REST call (after login)

```
1. Flutter  →  GET /market/candles?...   + Bearer JWT
2. Backend  →  validate JWT → load user
3. Backend  →  load Upstox access_token from DB (server-side only)
4. Backend  →  call Upstox REST (shared HTTP client, max 6 concurrent)
5. Backend  →  normalize response → Flutter-friendly JSON
6. Flutter  →  draws charts / cards
```

### C) Live ticks (WebSocket)

```
1. Flutter  →  ws://10.0.2.2:8040/ws/market?token=<JWT>
2. Backend  →  validate JWT + Upstox token
3. Backend  →  one Upstox Market Data Feed V3 connection per user (protobuf)
4. Flutter  →  { "action": "subscribe", "keys": [...], "mode": "ltpc" }
5. Backend  →  decode protobuf → fan-out JSON quotes to all of that user's sockets
```

Flutter does **not** open an Upstox WebSocket.

---

## 4. Folder map (what each piece is for)

```
optionedge_backend/
├── app/
│   ├── main.py              # Creates FastAPI app; mounts routers; lifespan
│   ├── config/              # Settings from .env (ports, secrets, redirect URI)
│   ├── database/            # SQLAlchemy engine + sessions (SQLite today)
│   ├── models/              # Tables: User, OAuthToken, OAuthLoginSession
│   ├── repositories/        # DB read/write helpers (no HTTP)
│   ├── schemas/             # Pydantic shapes returned to Flutter
│   ├── routes/              # HTTP endpoints (/auth, /market)
│   ├── dependencies/        # JWT → user; user → Upstox access_token
│   ├── services/            # Business logic + Upstox HTTP calls
│   │   ├── upstox_oauth.py  # Login URL + code exchange
│   │   ├── upstox_client.py # Quotes, chain, greeks, candles
│   │   ├── upstox_http.py   # Shared client + concurrency semaphore
│   │   └── market_*.py      # Normalize Upstox → Flutter JSON
│   ├── auth/                # JWT helpers + OAuth login-state store
│   ├── websocket/           # Upstox feed + hub + /ws/market
│   └── core/                # Instrument constants (NIFTY, VIX, …)
├── docs/                    # This overview + phase docs
├── .env                     # Secrets (gitignored) — never commit
└── requirements.txt
```

**Layering rule**

| Layer | Allowed to | Not allowed to |
|-------|------------|----------------|
| `routes/` | Call services, return schemas | Call Upstox directly / embed secrets |
| `services/` | Call Upstox + shape data | Know about Flutter widgets |
| `repositories/` | SQLAlchemy queries | HTTP |
| `dependencies/` | Auth / token injection | Business rules |

---

## 5. Data stored on the server

| Table / concept | What it holds | Flutter sees it? |
|-----------------|---------------|------------------|
| `users` | Upstox user id, email, name | Profile fields only |
| `oauth_tokens` | Upstox `access_token`, refresh/extended token, expiry | **Never** |
| `oauth_login_sessions` | CSRF `state` while login is in progress | Only status / JWT when done |
| Backend JWT | Signed with `JWT_SECRET_KEY`; proves “this Flutter session is user X” | Yes (stored on device) |

Upstox tokens typically expire around the broker’s daily cutoff (~03:30 IST). After that, user must login again.

---

## 6. HTTP API surface

### System

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | none | Liveness (`{"status":"ok"}`) — Flutter “online/offline” |
| GET | `/version` | none | App name / version / env |
| GET | `/docs` | none | Interactive Swagger UI |

### Auth (`app/routes/auth.py`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/auth/login` | none | Start OAuth → `authorization_url` + `state` |
| GET | `/auth/login/status?state=` | none | Poll until completed → backend JWT |
| GET | `/auth/callback` | browser | Upstox redirect target (HTML page) |
| GET | `/auth/profile` | JWT | Current user profile |
| POST | `/auth/logout` | JWT | Delete stored Upstox tokens |

### Market REST (`app/routes/market.py`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/market/quote?keys=` | JWT | Full quotes (defaults: VIX, NIFTY, BANKNIFTY, SENSEX) |
| GET | `/market/option-chain` | JWT | Option chain for underlying + expiry |
| GET | `/market/option-expiries` | JWT | Expiry list for picker |
| GET | `/market/greeks?keys=` | JWT | Option Greeks (Upstox V3) |
| GET | `/market/candles` | JWT | OHLCV (historical + intraday merge) |

All market routes:

1. Validate backend JWT  
2. Load Upstox access token from DB  
3. Call Upstox via `UpstoxClient`  
4. Return normalized JSON

### Market WebSocket (`app/websocket/`)

| Protocol | Path | Auth | Purpose |
|----------|------|------|---------|
| WS | `/ws/market?token=<JWT>` | JWT | Live quotes fan-out |

---

## 7. How a market request is secured (internally)

```
Flutter request
    │
    ▼
get_current_user()          # verify JWT → User (DB session opens & closes)
    │
    ▼
get_upstox_access_token()   # load Upstox token from DB (short-lived session)
    │
    ▼
Market*Service              # business logic
    │
    ▼
UpstoxClient                # HTTP to api.upstox.com
    │
    ▼
upstox_http_slot()          # shared client + max 6 concurrent Upstox calls
```

DB sessions are **not** held open while waiting on Upstox. That avoids SQLite / threadpool pile-ups when Flutter opens many requests.

---

## 8. Reliability: why “OFFLINE” used to happen

Flutter (especially option candles) can fire **many parallel** `/market/candles` and `/market/quote` calls.

Without limits, the backend:

1. Opened many TLS connections to Upstox at once  
2. Saturated the asyncio event loop  
3. Stopped answering `/health` quickly  
4. Flutter showed **OFFLINE** / `Connection refused`

**Mitigations in place today**

| Fix | Where | Effect |
|-----|-------|--------|
| SQLite `NullPool` | `app/database/session.py` | No 5-connection QueuePool exhaustion |
| Shared httpx + semaphore (6) | `app/services/upstox_http.py` | Cap outbound Upstox load |
| Short DB sessions in auth deps | `app/dependencies/*` | Don’t hold SQLite across Upstox waits |
| Candle pool (max 3) | Flutter `MarketRepositoryImpl` | Cap app-wide candle fan-out |

Prefer running uvicorn **without** `--reload` under heavy debug load for stability:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8040
```

---

## 9. Local run checklist (typical day)

1. **Start backend** on port `8040` (command above).  
2. **Start Cloudflare tunnel** (for OAuth HTTPS):

   ```powershell
   cloudflared tunnel --url http://127.0.0.1:8040
   ```

3. Copy the printed `https://….trycloudflare.com` URL.  
4. Set in `.env`:

   ```env
   UPSTOX_REDIRECT_URI=https://….trycloudflare.com/auth/callback
   ```

5. Paste the **same** Redirect URI into Upstox Developer Console → Save.  
6. Restart uvicorn so it reloads `.env` (no `--reload` means restart by hand).  
7. Flutter Base URL: `http://10.0.2.2:8040`.

**Quick tunnel caveat:** the hostname changes every tunnel restart → you must update Upstox console + `.env` again. A **named** Cloudflare tunnel (fixed hostname) is the permanent fix for that churn.

---

## 10. Config that matters (`.env`)

| Variable | Role |
|----------|------|
| `UPSTOX_API_KEY` | OAuth client id (public) |
| `UPSTOX_API_SECRET` | OAuth secret — **server only** |
| `UPSTOX_REDIRECT_URI` | Must match Upstox console exactly |
| `JWT_SECRET_KEY` | Signs Flutter JWTs |
| `DATABASE_URL` | Default SQLite file |
| `CORS_ORIGINS` | Browser clients (mobile apps ignore CORS) |
| `APP_PORT` / host | Bound by uvicorn CLI in practice |

Loaded by `app/config/settings.py` (Pydantic Settings).

---

## 11. What is done vs not done

| Area | Status |
|------|--------|
| Project setup, config, OAuth, JWT | Done |
| Market quote, option chain, greeks, candles | Done |
| WebSocket V3 fan-out | Done |
| Trading (place/modify/cancel orders) | Pending |
| Portfolio / positions APIs | Pending |
| Structured logging, full test suite | Pending |
| Production deploy (named tunnel / HTTPS / Postgres) | Pending |

---

## 12. Mental model for debugging

| Symptom | Likely cause |
|---------|----------------|
| Settings shows OFFLINE | Backend not running, wrong port, or hung under load |
| `Connection refused` on `10.0.2.2` | Nothing listening on host port / process died |
| Upstox `UDAPI10068` | `client_id` or `redirect_uri` mismatch vs console |
| Login works then market 401 | Upstox token expired — re-login |
| Tunnel DNS / NXDOMAIN | Old quick tunnel URL; start a new tunnel |

Useful checks:

```powershell
# Local liveness
Invoke-WebRequest http://127.0.0.1:8040/health

# Interactive API docs
start http://127.0.0.1:8040/docs
```

---

## 13. Related docs

| Doc | Topic |
|-----|-------|
| `docs/phase1_setup.md` | Venv, first run |
| `docs/phase2_configuration.md` | Settings / `.env` |
| `docs/phase3_oauth.md` | Upstox login flow |
| `docs/phase4_jwt.md` | Backend JWT |
| `docs/phase5_1_market_quote.md` | Quotes |
| `docs/phase5_2_option_chain.md` | Option chain |
| `docs/phase5_3_greeks.md` | Greeks |
| `docs/phase5_4_candles.md` | Candles |
| `docs/phase6_websocket_v3.md` | Live feed |

---

## 14. Bottom line

This backend is a **security and adapter layer**:

- **Security:** secrets and Upstox tokens stay on the server; Flutter gets a JWT.  
- **Adapter:** Upstox’s REST/WS shapes become simple Flutter JSON.  
- **Fan-out:** one Upstox market feed per user → many Flutter sockets.  
- **Stability:** concurrency caps so a busy option chain screen does not kill `/health`.
- **Historical foundation:** chunked, resumable full-history sync into SQLite with a
  chunk ledger and verified coverage, today-session reconstruction with correct
  forming/closed candles, one canonical candle reconciler shared by the historical,
  intraday and WebSocket sources, warmup-aware indicators, and a `can_trade` verdict
  that stays false until the data proves itself — see
  [historical_market_data.md](historical_market_data.md).

If you understand those jobs, you understand what this project is doing.
