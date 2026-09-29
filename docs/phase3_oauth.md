# Phase 3 — Upstox OAuth (Authorization Code Flow)

## Purpose

Let Flutter log users in with Upstox **without** ever seeing:
- `UPSTOX_API_SECRET`
- Upstox `access_token`
- Upstox `extended_token`

Your backend is the only system that talks to Upstox’s token endpoint.

---

## Architecture

```
Flutter                     Backend                         Upstox
  |                            |                               |
  | GET /auth/login            |                               |
  |--------------------------->|                               |
  | {authorization_url,state}  |                               |
  |<---------------------------|                               |
  | open authorization_url     |                               |
  |----------------------------------------------------------->|
  |                            |  GET /auth/callback?code&state|
  |                            |<------------------------------|
  |                            |  POST /v2/login/authorization/token
  |                            |------------------------------>|
  |                            |  access_token + profile       |
  |                            |<------------------------------|
  |                            |  save User + OAuthToken (DB)  |
  | GET /auth/login/status     |                               |
  |--------------------------->|                               |
  | {status,user} NO tokens    |                               |
  |<---------------------------|                               |
```

---

## Folder explanation

| Path | Role |
|------|------|
| `app/database/` | SQLite engine, sessions, `init_db()` |
| `app/models/user.py` | Local user linked to Upstox UCC |
| `app/models/oauth_token.py` | Stored Upstox tokens (server only) |
| `app/auth/oauth_state.py` | CSRF `state` + Flutter poll sessions |
| `app/services/upstox_oauth.py` | Build login URL + exchange code |
| `app/repositories/auth_repository.py` | DB upserts for user/token |
| `app/routes/auth.py` | HTTP endpoints |
| `app/schemas/auth.py` | Flutter JSON shapes |

---

## Endpoints

### 1) Start login

- **Endpoint:** `GET /auth/login`
- **Request body:** none
- **Success response:**

```json
{
  "authorization_url": "https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=...&redirect_uri=...&state=...",
  "state": "random-csrf-value"
}
```

- **Error (keys missing):** HTTP `503`

```json
{
  "detail": "UPSTOX_API_KEY and UPSTOX_API_SECRET must be set in .env ..."
}
```

### 2) Poll status (Flutter)

- **Endpoint:** `GET /auth/login/status?state=...`
- **Pending:**

```json
{ "status": "pending", "user": null, "error_message": null }
```

- **Completed:**

```json
{
  "status": "completed",
  "user": {
    "id": 1,
    "upstox_user_id": "ABCD12",
    "email": "user@example.com",
    "user_name": "Jane Trader",
    "broker": null,
    "user_type": null
  },
  "error_message": null
}
```

### 3) Callback (browser / WebView only)

- **Endpoint:** `GET /auth/callback?code=...&state=...`
- Returns a simple HTML success/failure page.
- Registered in Upstox console as Redirect URI.

### 4) Logout

- **Endpoint:** `POST /auth/logout`
- **Request body:**

```json
{ "user_id": 1 }
```

- **Response:**

```json
{ "detail": "logged_out" }
```

---

## Flutter example

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

Future<Map<String, dynamic>?> loginWithUpstox() async {
  final start = await http.get(Uri.parse('http://127.0.0.1:8000/auth/login'));
  if (start.statusCode != 200) {
    throw Exception(start.body);
  }
  final data = jsonDecode(start.body) as Map<String, dynamic>;
  final state = data['state'] as String;
  final url = Uri.parse(data['authorization_url'] as String);

  await launchUrl(url, mode: LaunchMode.externalApplication);

  // Poll until completed / failed / expired
  for (var i = 0; i < 60; i++) {
    await Future<void>.delayed(const Duration(seconds: 2));
    final statusRes = await http.get(
      Uri.parse('http://127.0.0.1:8000/auth/login/status?state=$state'),
    );
    final status = jsonDecode(statusRes.body) as Map<String, dynamic>;
    if (status['status'] == 'completed') {
      return status['user'] as Map<String, dynamic>;
    }
    if (status['status'] == 'failed' || status['status'] == 'expired') {
      throw Exception(status['error_message'] ?? status['status']);
    }
  }
  throw Exception('Login timed out');
}
```

---

## Upstox Developer Console setup (required)

1. Open [Upstox Developer Apps](https://account.upstox.com/developer/apps) (or your console URL).
2. Create an app (or open existing).
3. Copy **API Key** → `UPSTOX_API_KEY` in `.env`
4. Copy **API Secret** → `UPSTOX_API_SECRET` in `.env`
5. Set **Redirect URI** exactly to:

```text
http://127.0.0.1:8000/auth/callback
```

6. Save, then **restart uvicorn** (settings are cached at process start).

---

## Token expiry note

Upstox access tokens expire at **03:30 AM IST** (not a fixed “60 minutes”).
There is no classic OAuth refresh token in this flow — the user must log in again
after expiry (or use Upstox extended / other token products later).

---

## How to test Phase 3

1. Put real keys in `.env`.
2. Restart server.
3. Open http://127.0.0.1:8000/docs → **Auth** → `GET /auth/login` → Execute.
4. Copy `authorization_url` into a browser, log in to Upstox.
5. You should land on the HTML success page.
6. In Swagger, call `GET /auth/login/status` with the same `state`.
7. Confirm `optionedge.db` was created in the project root.

Without keys, `/auth/login` correctly returns **503** — that still proves the route works.

---

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Redirect URI mismatch | Must match Upstox console **exactly** |
| Keys in Flutter | Keep only in backend `.env` |
| Forgetting to restart after editing `.env` | Restart uvicorn |
| Expecting refresh_token | Upstox code flow uses daily 03:30 IST expiry |

---

## Next (Phase 4)

Issue a **JWT** to Flutter after OAuth completes so every later API call is authenticated without sending `user_id` in the body.
