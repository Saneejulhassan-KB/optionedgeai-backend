"""
Database engine and session factory.

Why this file exists
--------------------
SQLAlchemy needs:
  1. An Engine  — connection to SQLite/PostgreSQL
  2. A Session  — one unit of work per request (read/write then close)

How it communicates with other files
------------------------------------
- Reads DATABASE_URL from Settings
- Models import Base from app.database.base
- Routes use `Depends(get_db)` to get a Session

Best practices
--------------
- One session per request; always close it (finally / yield)
- check_same_thread=False is required for SQLite + FastAPI threads
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.database.base import Base

settings = get_settings()

# SQLite needs this flag when used with FastAPI's multi-threaded server.
_connect_args: dict[str, bool] = {}
_engine_kwargs: dict = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
    # Flutter debug opens many concurrent quote/chain/candle requests.
    # Default QueuePool (size 5) exhausts → TimeoutError → /health hangs → "OFFLINE".
    # NullPool opens/closes per request — correct for SQLite + high concurrency.
    _engine_kwargs["poolclass"] = NullPool

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    **_engine_kwargs,
    # echo=True  # uncomment to log every SQL statement while learning
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """
    Create all tables that inherit from Base.

    Phase 3 uses create_all for speed while learning.
    Phase 11 / production should prefer Alembic migrations instead.
    """
    # Import models so they register with Base.metadata before create_all.
    from app.models import (  # noqa: F401
        candle,
        historical_chunk,
        historical_coverage,
        oauth_login_session,
        oauth_token,
        user,
    )
    from app.database.schema_upgrade import upgrade_market_data_schema

    Base.metadata.create_all(bind=engine)
    upgrade_market_data_schema(engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency: yield a DB session, then close it.

    Usage in a route:
        def route(db: Session = Depends(get_db)):
            ...
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
