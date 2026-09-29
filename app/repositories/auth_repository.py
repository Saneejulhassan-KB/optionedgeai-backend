"""
User + OAuth token persistence helpers.

Why repositories exist
----------------------
Routes should not contain raw SQLAlchemy queries mixed with HTTP logic.
Repositories = "talk to the database only".
Services = "business rules + call Upstox + call repositories".
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.oauth_token import OAuthToken
from app.models.user import User
from app.utils.time_utils import upstox_access_token_expires_at


def get_user_by_upstox_id(db: Session, upstox_user_id: str) -> Optional[User]:
    """Find a user by Upstox UCC / user_id."""
    statement = select(User).where(User.upstox_user_id == upstox_user_id)
    return db.scalar(statement)


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.get(User, user_id)


def upsert_user_from_upstox_profile(
    db: Session,
    *,
    upstox_user_id: str,
    email: Optional[str],
    user_name: Optional[str],
    broker: Optional[str],
    user_type: Optional[str],
) -> User:
    """Create or update the local user from Upstox token-response profile fields."""
    user = get_user_by_upstox_id(db, upstox_user_id)
    if user is None:
        user = User(upstox_user_id=upstox_user_id)
        db.add(user)

    user.email = email
    user.user_name = user_name
    user.broker = broker
    user.user_type = user_type
    db.flush()  # Assign user.id without full commit yet
    return user


def upsert_oauth_token(
    db: Session,
    *,
    user: User,
    access_token: str,
    extended_token: Optional[str],
) -> OAuthToken:
    """
    Store (or replace) Upstox tokens for this user.

    One row per user in Phase 3 (unique user_id on oauth_tokens).
    """
    statement = select(OAuthToken).where(OAuthToken.user_id == user.id)
    token_row = db.scalar(statement)

    if token_row is None:
        token_row = OAuthToken(user_id=user.id, access_token=access_token)
        db.add(token_row)

    token_row.access_token = access_token
    token_row.extended_token = extended_token
    token_row.expires_at = upstox_access_token_expires_at()
    db.flush()
    return token_row


def delete_oauth_token_for_user(db: Session, user_id: int) -> bool:
    """Remove stored Upstox tokens (logout). Returns True if a row was deleted."""
    statement = select(OAuthToken).where(OAuthToken.user_id == user_id)
    token_row = db.scalar(statement)
    if token_row is None:
        return False
    db.delete(token_row)
    return True


def get_oauth_token_for_user(db: Session, user_id: int) -> Optional[OAuthToken]:
    """Return the stored Upstox token row for this user, if any."""
    statement = select(OAuthToken).where(OAuthToken.user_id == user_id)
    return db.scalar(statement)
