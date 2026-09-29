"""
Idempotent, additive schema reconciliation for the market-data tables.

`create_all` cannot add columns to a table that already exists, and this
project has no Alembic tree yet. Only additive ALTERs and a legacy status
rename are performed here; nothing is dropped or rewritten.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.models.historical_coverage import LEGACY_STATUS_MAP

logger = logging.getLogger(__name__)

# table → column → DDL type used when the column is missing.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "historical_coverage": {
        "provider": "VARCHAR(32) NOT NULL DEFAULT 'upstox'",
        "requested_from": "DATETIME",
        "requested_to": "DATETIME",
        "suspicious_gap_count": "INTEGER NOT NULL DEFAULT 0",
        "missing_ranges": "TEXT",
        "last_successful_sync": "DATETIME",
    },
}


def upgrade_market_data_schema(engine: Engine) -> None:
    """Add any missing market-data columns and normalize legacy status values."""
    _rebuild_candles_if_key_is_unusable(engine)

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name in present:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                logger.info("schema_upgrade_column_added table=%s column=%s", table, name)

        if "historical_coverage" in existing_tables:
            for legacy, current in LEGACY_STATUS_MAP.items():
                conn.execute(
                    text(
                        "UPDATE historical_coverage SET status = :current "
                        "WHERE status = :legacy"
                    ),
                    {"current": current, "legacy": legacy},
                )


def _rebuild_candles_if_key_is_unusable(engine: Engine) -> None:
    """
    Drop a `candles` table whose primary key SQLite cannot auto-increment.

    Early builds declared the key as BIGINT, which SQLite does not treat as a
    rowid alias, so every insert failed. Such a table is empty by definition
    and the candles it should hold are re-downloadable from Upstox.
    """
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    if "candles" not in inspector.get_table_names():
        return

    for column in inspector.get_columns("candles"):
        if column["name"] != "id":
            continue
        if str(column["type"]).upper().startswith("INTEGER"):
            return
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE candles"))
        logger.warning(
            "schema_upgrade_table_rebuilt table=candles reason=non_autoincrement_primary_key"
        )
        from app.database.base import Base

        Base.metadata.tables["candles"].create(bind=engine)
        return
