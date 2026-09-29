"""
OAuth `state` store backed by SQLite (shared across uvicorn processes).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import delete, select

from app.database.session import SessionLocal
from app.models.oauth_login_session import OAuthLoginSession

OAUTH_STATE_TTL: timedelta = timedelta(minutes=15)


class LoginStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class LoginSessionView:
    """Lightweight view object used by routes (same fields as before)."""

    def __init__(self, row: OAuthLoginSession) -> None:
        self.state = row.state
        self.status = LoginStatus(row.status)
        self.created_at = row.created_at
        self.user_id = row.user_id
        self.upstox_user_id = row.upstox_user_id
        self.user_name = row.user_name
        self.email = row.email
        self.access_token = row.access_token
        self.error_message = row.error_message
        self.expires_at = row.expires_at

    def is_expired(self) -> bool:
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(tz=timezone.utc) > expires


class OAuthStateStore:
    """Create / read / complete OAuth login sessions in the database."""

    def create(self) -> LoginSessionView:
        state = secrets.token_urlsafe(32)
        now = datetime.now(tz=timezone.utc)
        row = OAuthLoginSession(
            state=state,
            status=LoginStatus.PENDING.value,
            created_at=now,
            expires_at=now + OAUTH_STATE_TTL,
        )
        with SessionLocal() as db:
            self._cleanup(db)
            db.add(row)
            db.commit()
            db.refresh(row)
            return LoginSessionView(row)

    def get(self, state: str) -> Optional[LoginSessionView]:
        with SessionLocal() as db:
            row = db.get(OAuthLoginSession, state)
            if row is None:
                return None
            view = LoginSessionView(row)
            if view.is_expired() and row.status == LoginStatus.PENDING.value:
                row.status = LoginStatus.EXPIRED.value
                db.commit()
                db.refresh(row)
                return LoginSessionView(row)
            return view

    def mark_completed(
        self,
        state: str,
        *,
        user_id: int,
        upstox_user_id: str,
        user_name: Optional[str],
        email: Optional[str],
        access_token: str,
    ) -> Optional[LoginSessionView]:
        with SessionLocal() as db:
            row = db.get(OAuthLoginSession, state)
            if row is None:
                return None
            view = LoginSessionView(row)
            if view.is_expired() and row.status == LoginStatus.PENDING.value:
                row.status = LoginStatus.EXPIRED.value
                db.commit()
                return None
            row.status = LoginStatus.COMPLETED.value
            row.user_id = user_id
            row.upstox_user_id = upstox_user_id
            row.user_name = user_name
            row.email = email
            row.access_token = access_token
            db.commit()
            db.refresh(row)
            return LoginSessionView(row)

    def mark_failed(self, state: str, message: str) -> None:
        with SessionLocal() as db:
            row = db.get(OAuthLoginSession, state)
            if row is None:
                return
            row.status = LoginStatus.FAILED.value
            row.error_message = message
            db.commit()

    def _cleanup(self, db) -> None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        db.execute(delete(OAuthLoginSession).where(OAuthLoginSession.created_at < cutoff))


oauth_state_store = OAuthStateStore()
