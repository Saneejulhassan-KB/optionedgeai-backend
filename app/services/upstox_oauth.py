"""
Upstox OAuth service — Authorization Code Flow.

Why this file exists
--------------------
All talk with Upstox login/token endpoints lives here.
Routes stay thin: parse HTTP → call service → return schema.

Security rules
--------------
- client_secret is read from Settings only (never from Flutter)
- access_token is saved in DB and never returned to Flutter
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.auth.jwt import create_access_token
from app.auth.oauth_state import LoginSessionView, oauth_state_store
from app.config import Settings, get_settings
from app.repositories import auth_repository
from app.schemas.auth import LoginStartResponse, UserPublic


class UpstoxOAuthError(Exception):
    """Raised when Upstox returns an error or config is incomplete."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UpstoxOAuthService:
    """Build login URLs and exchange authorization codes for tokens."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def _require_credentials(self) -> None:
        if not self.settings.upstox_api_key or not self.settings.upstox_api_secret:
            raise UpstoxOAuthError(
                "UPSTOX_API_KEY and UPSTOX_API_SECRET must be set in .env "
                "before starting OAuth. Get them from the Upstox Developer Console.",
                status_code=503,
            )

    def start_login(self) -> LoginStartResponse:
        """
        Step 1 — create state + authorization URL for Flutter.
        """
        self._require_credentials()
        session: LoginSessionView = oauth_state_store.create()

        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.upstox_api_key,
                "redirect_uri": self.settings.upstox_redirect_uri,
                "state": session.state,
            }
        )
        authorization_url = f"{self.settings.upstox_auth_url}?{query}"
        return LoginStartResponse(
            authorization_url=authorization_url,
            state=session.state,
        )

    async def complete_login(
        self,
        db: Session,
        *,
        code: str,
        state: str,
    ) -> UserPublic:
        """
        Step 2 — verify state, exchange code, upsert user + tokens.
        """
        self._require_credentials()

        session = oauth_state_store.get(state)
        if session is None:
            raise UpstoxOAuthError("Invalid or unknown OAuth state.", status_code=400)
        if session.is_expired():
            oauth_state_store.mark_failed(state, "Login session expired. Start again.")
            raise UpstoxOAuthError("OAuth state expired. Call /auth/login again.", status_code=400)

        token_payload = await self._exchange_code_for_token(code)
        access_token = token_payload.get("access_token")
        upstox_user_id = token_payload.get("user_id")

        if not access_token or not upstox_user_id:
            oauth_state_store.mark_failed(state, "Upstox token response missing fields.")
            raise UpstoxOAuthError(
                "Upstox did not return access_token/user_id.",
                status_code=502,
            )

        user = auth_repository.upsert_user_from_upstox_profile(
            db,
            upstox_user_id=str(upstox_user_id),
            email=token_payload.get("email"),
            user_name=token_payload.get("user_name"),
            broker=token_payload.get("broker"),
            user_type=token_payload.get("user_type"),
        )
        auth_repository.upsert_oauth_token(
            db,
            user=user,
            access_token=str(access_token),
            extended_token=token_payload.get("extended_token"),
        )
        db.commit()
        db.refresh(user)

        # Phase 4 — issue OUR JWT for Flutter (never the Upstox token)
        app_jwt = create_access_token(
            user_id=user.id,
            upstox_user_id=user.upstox_user_id,
        )

        oauth_state_store.mark_completed(
            state,
            user_id=user.id,
            upstox_user_id=user.upstox_user_id,
            user_name=user.user_name,
            email=user.email,
            access_token=app_jwt,
        )
        return UserPublic.model_validate(user)

    async def _exchange_code_for_token(self, code: str) -> dict[str, Any]:
        """
        POST to Upstox token endpoint (server-to-server only).
        """
        url = f"{self.settings.upstox_base_url.rstrip('/')}/v2/login/authorization/token"
        headers = {
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        form = {
            "code": code,
            "client_id": self.settings.upstox_api_key,
            "client_secret": self.settings.upstox_api_secret,
            "redirect_uri": self.settings.upstox_redirect_uri,
            "grant_type": "authorization_code",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, data=form)

        if response.status_code >= 400:
            # Do not leak client_secret; include Upstox body for debugging only in logs later
            detail = response.text
            raise UpstoxOAuthError(
                f"Upstox token exchange failed ({response.status_code}): {detail}",
                status_code=502,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise UpstoxOAuthError("Unexpected Upstox token response type.", status_code=502)
        return payload

    def logout(self, db: Session, user_id: int) -> bool:
        """Delete stored Upstox tokens for this user."""
        deleted = auth_repository.delete_oauth_token_for_user(db, user_id)
        if deleted:
            db.commit()
        return deleted
