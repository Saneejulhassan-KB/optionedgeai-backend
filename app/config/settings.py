"""
Application settings — loaded from environment variables / .env
==============================================================

Why this file exists
--------------------
Hardcoding secrets and URLs in Python is dangerous and inflexible.
This module reads configuration once from the environment and exposes a
typed `Settings` object that the rest of the app imports.

How it communicates with other files
------------------------------------
- Reads:  `.env` (via pydantic-settings) and real OS environment variables
- Used by: `app.main` (app title, CORS), and later auth / database / Upstox services

Design choices
--------------
- pydantic-settings: validates types (bool, int) and fails fast on bad values
- `get_settings()` cached with lru_cache: load .env once per process, not per request
- Secrets stay as fields here but are NEVER returned from public API responses

Common mistakes
---------------
- Committing a real `.env` to Git (prevented by `.gitignore`)
- Using invalid CORS patterns like `http://localhost:*` (browsers reject this)
- Reading os.getenv() scattered across files instead of one Settings class
"""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always load .env from the project root (not depending on shell cwd).
# Reloading this module picks up UPSTOX_REDIRECT_URI changes after tunnel restarts.
# Tunnel refresh marker: 2026-08-13-1420
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_ENV_FILE: Path = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """
    Typed configuration for OptionEdgeAI Backend.

    Field names match keys in `.env` (case-insensitive by default).
    Example: APP_NAME in .env → settings.app_name in Python.
    """

    # Tell pydantic-settings where to load values from.
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore unknown env keys instead of crashing
    )

    # --- Application ---
    app_name: str = Field(default="OptionEdgeAI", description="Product name")
    app_env: str = Field(default="development", description="development | staging | production")
    app_debug: bool = Field(default=True, description="Extra error detail when True")
    app_host: str = Field(default="0.0.0.0", description="Bind address for Uvicorn")
    app_port: int = Field(default=8000, description="HTTP port")
    app_version: str = Field(default="0.1.0", description="API version string")

    # --- CORS (browser / Flutter web only; mobile apps ignore CORS) ---
    # Comma-separated origins in .env, e.g.:
    #   CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
    # Use * alone to allow all origins (dev only; never in production with credentials).
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        description="Comma-separated allowed browser origins, or *",
    )

    # --- Database ---
    database_url: str = Field(
        default="sqlite:///./optionedge.db",
        description="SQLAlchemy database URL",
    )

    # --- JWT (used from Phase 4 onward) ---
    jwt_secret_key: str = Field(
        default="change-me-to-a-long-random-secret",
        description="Secret used to sign Flutter session JWTs",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT signing algorithm")
    jwt_access_token_expire_minutes: int = Field(
        default=60,
        description="How long a Flutter JWT stays valid",
    )

    # --- Upstox OAuth (filled in Phase 3; empty is OK for now) ---
    upstox_api_key: str = Field(default="", description="Upstox API key (public client id)")
    upstox_api_secret: str = Field(default="", description="Upstox API secret — NEVER expose")
    upstox_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/auth/callback",
        description="OAuth redirect URI registered in Upstox console",
    )
    upstox_base_url: str = Field(
        default="https://api.upstox.com",
        description="Upstox REST API base URL",
    )
    upstox_auth_url: str = Field(
        default="https://api.upstox.com/v2/login/authorization/dialog",
        description="Upstox OAuth authorization dialog URL",
    )

    # --- Optional Redis ---
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for cache / WebSocket fan-out (optional)",
    )

    # --- Historical market-data foundation ---
    historical_sync_enabled: bool = Field(
        default=True,
        description="Allow POST /api/v1/market/sync to download Upstox history",
    )
    historical_max_retries: int = Field(
        default=3,
        description="Retries per historical chunk on transient Upstox errors",
    )
    historical_timeframes: str = Field(
        default="3m,5m,15m,1D",
        description="Comma-separated canonical timeframes to sync",
    )
    historical_instruments: str = Field(
        default="NSE_INDEX|Nifty 50,NSE_INDEX|Nifty Bank,BSE_INDEX|SENSEX",
        description="Comma-separated Upstox instrument_keys to sync",
    )
    active_candle_lookback: int = Field(
        default=600,
        description="Bars loaded into active context for indicators/structure",
    )
    data_stale_threshold_seconds: int = Field(
        default=120,
        description="Mark market DEGRADED when last candle older than this",
    )

    # --- Candle lifecycle / readiness ---
    live_timeframes: str = Field(
        default="3m,5m,15m",
        description="Timeframes updated from live WebSocket ticks",
    )
    reconnect_backfill_timeframes: str = Field(
        default="3m,5m,15m",
        description="Timeframes re-requested from Upstox after a WS reconnect",
    )
    trading_timeframes: str = Field(
        default="3m,5m,15m",
        description="Timeframes that must be ready before can_trade is true",
    )
    primary_timeframe: str = Field(
        default="5m",
        description="Timeframe used for the headline MarketState",
    )
    indicator_warmup_multiplier: float = Field(
        default=2.0,
        description="Bars required per indicator period before it counts as READY",
    )
    coverage_gap_scan_days: int = Field(
        default=30,
        description="Recent window scanned for suspicious intraday candle gaps",
    )
    live_candle_persist_interval_seconds: float = Field(
        default=2.0,
        description="Minimum seconds between forming-candle writes per instrument",
    )

    @property
    def cors_origin_list(self) -> List[str]:
        """
        Parse CORS_ORIGINS into a list FastAPI can use.

        Examples:
            "*" → ["*"]
            "http://a.com, http://b.com" → ["http://a.com", "http://b.com"]
        """
        raw: str = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        """True when running locally (relaxed security is acceptable)."""
        return self.app_env.lower() in {"development", "dev", "local"}

    @field_validator("app_port")
    @classmethod
    def port_must_be_valid(cls, value: int) -> int:
        """Reject impossible TCP ports early at startup."""
        if not 1 <= value <= 65535:
            raise ValueError("APP_PORT must be between 1 and 65535")
        return value


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    Why cache?
    - Reading and validating .env on every request would be wasteful.
    - One process → one settings object is enough.

    Usage:
        from app.config.settings import get_settings
        settings = get_settings()
    """
    return Settings()
