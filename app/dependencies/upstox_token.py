"""
Resolve a valid Upstox access_token for the current Flutter user.

Used by market routes (Phase 5+). Never returned to Flutter.

Opens a short-lived DB session and closes it before the route awaits Upstox,
so SQLite connections are not held across outbound HTTP.
"""

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status

from app.database.session import SessionLocal
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.repositories import auth_repository


def get_upstox_access_token(
    current_user: User = Depends(get_current_user),
) -> str:
    """
    Load Upstox access_token for the JWT user.

    Raises 401 if missing or past expires_at (user must re-login via OAuth).
    """
    db = SessionLocal()
    try:
        token_row = auth_repository.get_oauth_token_for_user(db, current_user.id)
        if token_row is None or not token_row.access_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No Upstox session found. Please login again.",
            )

        expires_at = token_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        now = datetime.now(tz=timezone.utc)
        if expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Upstox access token expired (03:30 IST cutoff). Please login again.",
            )

        return token_row.access_token
    finally:
        db.close()
