"""
OAuth token ORM model — stores Upstox access / extended tokens server-side.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class OAuthToken(Base):
    """
    Upstox tokens for one user.

    SECURITY:
    - Never return access_token or extended_token in Flutter API responses.
    - Phase 3 stores plaintext in SQLite for learning. Before production,
      encrypt at rest (or use a secrets vault). Flutter never sees these values.
    """

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        unique=True,  # One active token row per user for Phase 3 simplicity
    )

    # Long strings — Text, not String(255)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    extended_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Upstox tokens expire at 03:30 IST the following trading window
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship("User", back_populates="oauth_tokens")

    def __repr__(self) -> str:
        return f"<OAuthToken user_id={self.user_id} expires_at={self.expires_at}>"
