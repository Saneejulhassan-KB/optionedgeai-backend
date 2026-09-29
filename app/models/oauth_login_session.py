"""
Persisted OAuth login session (CSRF state) — survives restarts / multi-port.

Why DB instead of memory
------------------------
In-memory state fails when:
  - uvicorn reloads
  - Cloudflare tunnel hits a different port/process than /auth/login
  - PC sleeps and process restarts mid-login

Phase 4+ stores Flutter JWT elsewhere; this table is only for the
short OAuth handshake (pending → completed).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class OAuthLoginSession(Base):
    """One row per OAuth `state` value during login."""

    __tablename__ = "oauth_login_sessions"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)

    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    upstox_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Backend JWT issued at completion (not Upstox token)
    access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
