# OptionEdgeAI Backend

Production-grade FastAPI backend for the **OptionEdgeAI** Flutter trading app.

This server is the only system Flutter talks to. Upstox API secrets, OAuth secrets,
and refresh tokens live here — never inside the mobile app.

**Start here for the full picture:** [docs/architecture_overview.md](docs/architecture_overview.md)
(how OAuth, JWT, market APIs, WebSocket, tunnels, and the OFFLINE hang fix all fit together).

**Historical market-data foundation:** [docs/historical_market_data.md](docs/historical_market_data.md)
(`POST /api/v1/market/sync`, bootstrap, coverage, indicators, readiness).

---

## Phase status

| Phase | Topic              | Status        |
|-------|--------------------|---------------|
| 1     | Project setup      | ✅ Done        |
| 2     | Configuration      | ✅ Done        |
| 3     | OAuth (Upstox)     | ✅ Done        |
| 4     | JWT authentication | ✅ Done        |
| 5     | Market APIs        | ✅ Quote + Chain + Greeks + Candles |
| 6     | WebSocket          | ✅ V3 fan-out + real feed state + multi-timeframe reconnect backfill |
| 5b    | Historical foundation | ✅ Verified coverage + candle lifecycle + readiness |
| 7     | Trading APIs       | Pending       |
| 8     | Portfolio APIs     | Pending       |
| 9     | Logging            | Pending       |
| 10    | Testing            | Partial (sync, coverage, lifecycle, readiness, timezone) |
| 11    | Deployment         | Pending       |

---

## Prerequisites

- **Python 3.13** (recommended) — use the Windows Python install manager:
  ```powershell
  py install 3.13
  py -3.13 --version
  ```
  Avoid **3.14** for now: many packages still lack Windows wheels and install fails.
- A terminal (PowerShell is fine)

---

## Phase 1 — Setup (Windows / PowerShell)

### 1. Open the project folder

```powershell
cd c:\Projects\optionedge_backend
```

### 2. Create a virtual environment (must use Python 3.13)

```powershell
py -3.13 -m venv .venv
```

This creates a folder `.venv` with an isolated Python + pip for this project only.
Do **not** use plain `python -m venv` if that points at 3.14.

### 3. Activate the virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
```

Your prompt should show `(.venv)`. If PowerShell blocks the script, run once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again.

### 4. Install dependencies

```powershell
pip install -r requirements.txt
```

### 5. Confirm `.env` exists

A starter `.env` is already created from `.env.example`.
You do **not** need Upstox keys yet for Phase 1.

### 6. Start the server

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Meaning of that command:

| Part | Meaning |
|------|---------|
| `uvicorn` | The ASGI server |
| `app.main:app` | Module `app/main.py`, variable named `app` |
| `--reload` | Auto-restart when you edit Python files (dev only) |
| `--host 0.0.0.0` | Accept connections from other devices on your LAN (useful for a real phone) |
| `--port 8000` | Listen on port 8000 |

### 7. Test in the browser

- Health: http://127.0.0.1:8000/health  
- Version: http://127.0.0.1:8000/version  
- Interactive docs: http://127.0.0.1:8000/docs  

---

## Folder map (why each exists)

```
optionedge_backend/
├── app/
│   ├── main.py           # Creates FastAPI app; Uvicorn entry point
│   ├── api/              # Thin HTTP adapters (later)
│   ├── auth/             # Upstox OAuth + JWT (Phases 3–4)
│   ├── config/           # Settings from .env (Phase 2)
│   ├── database/         # SQLAlchemy engine / sessions
│   ├── middleware/       # CORS, rate limit, request logging
│   ├── models/           # Database tables
│   ├── repositories/     # DB queries only
│   ├── schemas/          # Pydantic request/response shapes
│   ├── services/         # Business logic + Upstox calls
│   ├── websocket/        # One Upstox feed → many Flutter clients
│   ├── utils/            # Shared helpers
│   ├── routes/           # Route modules mounted in main.py
│   ├── dependencies/     # FastAPI Depends() (DB session, current user)
│   └── core/             # Constants, security primitives
├── tests/                # Pytest suite (Phase 10)
├── docs/                 # Human-readable architecture notes
├── requirements.txt      # pip dependencies
├── .env.example          # Safe template of env vars
├── .env                  # Local secrets (gitignored)
└── README.md             # This file
```

---

## Flutter quick test (Phase 1)

```dart
import 'package:http/http.dart' as http;
import 'dart:convert';

Future<void> checkBackendHealth() async {
  // Android emulator → host machine: use 10.0.2.2 instead of 127.0.0.1
  // iOS simulator / Windows desktop: 127.0.0.1 is fine
  final uri = Uri.parse('http://127.0.0.1:8000/health');
  final response = await http.get(uri);

  if (response.statusCode == 200) {
    final data = jsonDecode(response.body) as Map<String, dynamic>;
    print(data); // {status: ok}
  } else {
    print('Health check failed: ${response.statusCode}');
  }
}
```

---

## Security rules (non-negotiable)

1. Never put Upstox **API Secret** in Flutter.
2. Never commit `.env`.
3. Flutter only receives a **backend-issued JWT** after OAuth completes.
4. Upstox access/refresh tokens are stored on the server only.

---

## Next step

**Phase 2 — Configuration:** load `.env` with Pydantic Settings, typed config object,
and wire CORS from environment variables.
