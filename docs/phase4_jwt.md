# Phase 4 — JWT Authentication

## Purpose

After Upstox OAuth succeeds, issue a **backend JWT** to Flutter so every later
API call is authenticated without sending `user_id` in the body.

Flutter still never sees Upstox `access_token` / API secret.

---

## Architecture

```
OAuth callback success
        │
        ▼
 create_access_token(user_id)   ← signed with JWT_SECRET_KEY
        │
        ▼
 GET /auth/login/status
   { status, user, access_token, token_type: "bearer" }
        │
        ▼
 Flutter stores JWT → Authorization: Bearer <jwt>
        │
        ├── GET  /auth/profile
        ├── POST /auth/logout
        └── (Phase 5+) /market/*
```

---

## Endpoints

### Login status (updated)

`GET /auth/login/status?state=...`

Completed response:

```json
{
  "status": "completed",
  "user": {
    "id": 1,
    "upstox_user_id": "XXXX",
    "user_name": "GOPIKRISHNAN A",
    "email": null,
    "broker": null,
    "user_type": null
  },
  "access_token": "<backend-jwt>",
  "token_type": "bearer",
  "error_message": null
}
```

### Profile (new)

`GET /auth/profile`  
Header: `Authorization: Bearer <jwt>`

```json
{
  "id": 1,
  "upstox_user_id": "XXXX",
  "user_name": "GOPIKRISHNAN A",
  "email": null,
  "broker": null,
  "user_type": null
}
```

### Logout (updated)

`POST /auth/logout`  
Header: `Authorization: Bearer <jwt>`  
Body: none

```json
{ "detail": "logged_out" }
```

### Error (missing/invalid JWT)

HTTP `401`

```json
{ "detail": "Invalid or expired access token." }
```

---

## Flutter

- Save `access_token` from login status into Hive (`auth_jwt`)
- Call `dio.updateToken(jwt)`
- On app start: restore JWT → `GET /auth/profile` (drop session if 401)
- Logout: `POST /auth/logout` then clear Hive + Dio header

---

## How to test (Swagger)

1. Open http://127.0.0.1:8020/docs  
2. Complete a real login from the app (so a JWT is issued), **or** temporarily mint one in Python for user id 1.  
3. Click **Authorize** in Swagger → paste `Bearer <jwt>`  
4. Call `GET /auth/profile` → your user  
5. Call `POST /auth/logout` → logged_out  

Or from Flutter: Log out → Login again → message should say **Login completed (JWT session)**.

---

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Sending Upstox token as Bearer | Use backend JWT from `/auth/login/status` |
| Keeping old "Restored local session" without JWT | Log out once; login again under Phase 4 |
| Forgetting to restart backend after changing `JWT_SECRET_KEY` | Restart uvicorn |

---

## Next

**Phase 5.1 — Market Quote** using `Depends(get_current_user)` + stored Upstox token.
