"""
Pydantic schemas for Auth / OAuth / JWT endpoints (Flutter-facing shapes).
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.auth.oauth_state import LoginStatus


class LoginStartResponse(BaseModel):
    """Returned by GET /auth/login — Flutter opens authorization_url."""

    authorization_url: str = Field(
        ...,
        description="Full Upstox login URL to open in browser / WebView",
    )
    state: str = Field(
        ...,
        description="CSRF token; Flutter must poll /auth/login/status with this value",
    )


class UserPublic(BaseModel):
    """Safe user profile — NEVER includes Upstox access tokens."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    upstox_user_id: str
    email: Optional[str] = None
    user_name: Optional[str] = None
    broker: Optional[str] = None
    user_type: Optional[str] = None


class LoginStatusResponse(BaseModel):
    """
    Flutter polls this until status is completed or failed.

    When completed, `access_token` is the backend JWT (not Upstox).
    """

    status: LoginStatus
    user: Optional[UserPublic] = None
    access_token: Optional[str] = Field(
        default=None,
        description="Backend JWT — present only when status=completed",
    )
    token_type: Optional[str] = Field(
        default=None,
        description="Always 'bearer' when access_token is present",
    )
    error_message: Optional[str] = None


class LogoutResponse(BaseModel):
    detail: str = "logged_out"


class MessageResponse(BaseModel):
    detail: str
