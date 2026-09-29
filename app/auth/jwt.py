"""
JWT helpers for Flutter session tokens.

Why this file exists
--------------------
After Upstox OAuth completes, we issue OUR own JWT to Flutter.
Flutter never receives the Upstox access_token.

Flow:
  OAuth success → create_access_token(user_id) → Flutter stores JWT
  Later requests → Authorization: Bearer <jwt> → decode → load User

Security
--------
- Signed with JWT_SECRET_KEY from .env (never ship this to Flutter)
- Short expiry (JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
- sub claim = internal user id (integer as string)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt.exceptions import InvalidTokenError

from app.config import get_settings


class JwtError(Exception):
    """Raised when a JWT cannot be created or verified."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def create_access_token(
    *,
    user_id: int,
    upstox_user_id: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Create a signed JWT for Flutter.

    Claims:
      sub              → user.id (string)
      upstox_user_id   → broker UCC
      iat / exp        → issued / expiry (UTC)
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "upstox_user_id": upstox_user_id,
        "iat": now,
        "exp": expire,
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Verify signature + expiry and return claims.

    Raises JwtError on any failure (invalid, expired, wrong type).
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except InvalidTokenError as exc:
        raise JwtError("Invalid or expired access token.") from exc

    if payload.get("type") != "access":
        raise JwtError("Wrong token type.")

    if payload.get("sub") is None:
        raise JwtError("Token missing subject.")

    return payload


def user_id_from_token(token: str) -> int:
    """Decode JWT and return internal user id as int."""
    payload = decode_access_token(token)
    try:
        return int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise JwtError("Token subject is not a valid user id.") from exc
