"""
SQLAlchemy declarative base.

Why this file exists
--------------------
All database table models inherit from one Base so Alembic (later) and
`Base.metadata.create_all()` know every table in the project.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Root class for all ORM models (User, OAuthToken, ...)."""

    pass
