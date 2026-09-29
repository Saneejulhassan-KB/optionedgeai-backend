"""
FastAPI auth dependencies — resolve the current user from a Bearer JWT.

Why this file exists
--------------------
Routes that need a logged-in user declare:
    user: User = Depends(get_current_user)

FastAPI injects the User after validating Authorization: Bearer <jwt>.

Sessions are opened and closed inside the dependency (not via yield get_db)
so SQLite connections are not held open while the route awaits Upstox.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import JwtError, user_id_from_token
from app.database.session import SessionLocal
from app.models.user import User
from app.repositories import auth_repository

# auto_error=False so we can return a clear 401 JSON ourselves
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> User | None:
    """
    Resolve the user when a valid JWT is present, otherwise return None.

    Lets read-only diagnostics (market bootstrap) report per-user feed health
    without forcing authentication.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        user_id = user_id_from_token(credentials.credentials)
    except JwtError:
        return None

    db = SessionLocal()
    try:
        user = auth_repository.get_user_by_id(db, user_id)
        if user is None:
            return None
        db.expunge(user)
        return user
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> User:
    """
    Require a valid Flutter JWT and return the matching User row.

    Used by: GET /auth/profile, POST /auth/logout, and later market routes.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Send Authorization: Bearer <jwt>.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = user_id_from_token(credentials.credentials)
    except JwtError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    db = SessionLocal()
    try:
        user = auth_repository.get_user_by_id(db, user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found for this token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Detach so the session can close before the route awaits Upstox.
        db.expunge(user)
        return user
    finally:
        db.close()
