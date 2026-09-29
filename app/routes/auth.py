"""
Auth HTTP routes — OAuth login + JWT session for Flutter.

Endpoints
---------
GET  /auth/login              → authorization URL + state
GET  /auth/login/status       → poll until completed (+ JWT)
GET  /auth/callback           → Upstox redirect target (browser)
GET  /auth/profile            → current user (JWT required)
POST /auth/logout             → wipe Upstox tokens (JWT required)
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth.oauth_state import LoginStatus, oauth_state_store
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.auth import (
    LoginStartResponse,
    LoginStatusResponse,
    LogoutResponse,
    UserPublic,
)
from app.services.upstox_oauth import UpstoxOAuthError, UpstoxOAuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


def get_oauth_service() -> UpstoxOAuthService:
    """Dependency wrapper — makes testing easier later."""
    return UpstoxOAuthService()


@router.get(
    "/login",
    response_model=LoginStartResponse,
    summary="Start Upstox OAuth login",
)
def start_login(
    service: UpstoxOAuthService = Depends(get_oauth_service),
) -> LoginStartResponse:
    """
    Flutter calls this first, then opens authorization_url and polls status.
    """
    try:
        return service.start_login()
    except UpstoxOAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get(
    "/login/status",
    response_model=LoginStatusResponse,
    summary="Poll OAuth login status (returns JWT when completed)",
)
def login_status(state: str = Query(..., min_length=8)) -> LoginStatusResponse:
    """
    When status is completed, response includes:
      access_token  → backend JWT
      token_type    → "bearer"
      user          → safe profile

    Flutter must store access_token and send it as:
      Authorization: Bearer <access_token>
    """
    session = oauth_state_store.get(state)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown state. Call /auth/login first.",
        )

    user: UserPublic | None = None
    access_token: str | None = None
    token_type: str | None = None

    if session.status == LoginStatus.COMPLETED and session.user_id is not None:
        user = UserPublic(
            id=session.user_id,
            upstox_user_id=session.upstox_user_id or "",
            email=session.email,
            user_name=session.user_name,
        )
        access_token = session.access_token
        if access_token:
            token_type = "bearer"

    return LoginStatusResponse(
        status=session.status,
        user=user,
        access_token=access_token,
        token_type=token_type,
        error_message=session.error_message,
    )


@router.get(
    "/callback",
    summary="Upstox OAuth redirect callback",
    response_class=HTMLResponse,
)
async def oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
    service: UpstoxOAuthService = Depends(get_oauth_service),
) -> HTMLResponse:
    """Upstox redirects the browser here after the user logs in."""
    if error:
        if state:
            oauth_state_store.mark_failed(state, error)
        return _html_page(
            title="Login failed",
            body=f"Upstox returned an error: {error}",
            ok=False,
        )

    if not code or not state:
        return _html_page(
            title="Login failed",
            body="Missing code or state query parameter.",
            ok=False,
        )

    try:
        user = await service.complete_login(db, code=code, state=state)
    except UpstoxOAuthError as exc:
        if state:
            oauth_state_store.mark_failed(state, exc.message)
        return _html_page(title="Login failed", body=exc.message, ok=False)

    return _html_page(
        title="Login successful",
        body=(
            f"Welcome {user.user_name or user.upstox_user_id}. "
            "You can return to the OptionEdgeAI app."
        ),
        ok=True,
    )


@router.get(
    "/profile",
    response_model=UserPublic,
    summary="Current user profile (JWT required)",
)
def get_profile(current_user: User = Depends(get_current_user)) -> UserPublic:
    """
    Example:
      GET /auth/profile
      Authorization: Bearer <jwt>
    """
    return UserPublic.model_validate(current_user)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Logout (JWT required) — deletes stored Upstox tokens",
)
def logout(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    service: UpstoxOAuthService = Depends(get_oauth_service),
) -> LogoutResponse:
    """
    Requires Authorization: Bearer <jwt>.
    Deletes Upstox tokens for this user from our database.
    Flutter should discard its JWT after calling this.
    """
    service.logout(db, current_user.id)
    return LogoutResponse(detail="logged_out")


def _html_page(*, title: str, body: str, ok: bool) -> HTMLResponse:
    """Simple HTML for the WebView / browser after Upstox redirects."""
    color = "#0a7a32" if ok else "#a12020"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; padding: 2rem; color: #111; }}
    h1 {{ color: {color}; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{body}</p>
  <p>You may close this window.</p>
</body>
</html>"""
    status_code = 200 if ok else 400
    return HTMLResponse(content=html, status_code=status_code)
