# File: /backend/app/core/schema_readiness.py
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..config import get_settings

REQUIRED_ENUM_VALUES: dict[str, set[str]] = {
    "notice_channel": {"POPUP", "BANNER", "POPUP_BANNER", "NONE", "BOARD"},
    "serial_acquisition_type": {"UNCLASSIFIED", "DONATION", "SUBSCRIPTION"},
}

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "serial_publications": {"shelf_id", "shelf_row_end", "shelf_column_end"},
    "serial_layouts": {"walls"},
    "serial_shelf_types": {"color"},
}

POSTGRES_COLUMN_DEFINITIONS: dict[tuple[str, str], str] = {
    ("serial_publications", "shelf_id"): "UUID",
    ("serial_publications", "shelf_row_end"): "INTEGER",
    ("serial_publications", "shelf_column_end"): "INTEGER",
    ("serial_layouts", "walls"): "JSONB",
    ("serial_shelf_types", "color"): "VARCHAR",
}

SQLITE_COLUMN_DEFINITIONS: dict[tuple[str, str], str] = {
    ("serial_publications", "shelf_id"): "TEXT",
    ("serial_publications", "shelf_row_end"): "INTEGER",
    ("serial_publications", "shelf_column_end"): "INTEGER",
    ("serial_layouts", "walls"): "TEXT",
    ("serial_shelf_types", "color"): "VARCHAR",
}


def _dialect_name(db: Session) -> str:
    return db.get_bind().dialect.name


def ensure_startup_schema(db: Session) -> None:
    """Apply small, idempotent production safety schema patches."""
    dialect = _dialect_name(db)
    if dialect == "postgresql":
        db.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notice_channel') THEN
                        CREATE TYPE notice_channel AS ENUM ('POPUP', 'BANNER', 'POPUP_BANNER', 'NONE', 'BOARD');
                    ELSE
                        ALTER TYPE notice_channel ADD VALUE IF NOT EXISTS 'POPUP';
                        ALTER TYPE notice_channel ADD VALUE IF NOT EXISTS 'BANNER';
                        ALTER TYPE notice_channel ADD VALUE IF NOT EXISTS 'POPUP_BANNER';
                        ALTER TYPE notice_channel ADD VALUE IF NOT EXISTS 'NONE';
                        ALTER TYPE notice_channel ADD VALUE IF NOT EXISTS 'BOARD';
                    END IF;

                    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'serial_acquisition_type') THEN
                        CREATE TYPE serial_acquisition_type AS ENUM ('UNCLASSIFIED', 'DONATION', 'SUBSCRIPTION');
                    ELSE
                        ALTER TYPE serial_acquisition_type ADD VALUE IF NOT EXISTS 'UNCLASSIFIED';
                        ALTER TYPE serial_acquisition_type ADD VALUE IF NOT EXISTS 'DONATION';
                        ALTER TYPE serial_acquisition_type ADD VALUE IF NOT EXISTS 'SUBSCRIPTION';
                    END IF;

                    ALTER TABLE IF EXISTS serial_publications
                        ADD COLUMN IF NOT EXISTS shelf_id UUID,
                        ADD COLUMN IF NOT EXISTS shelf_row_end INTEGER,
                        ADD COLUMN IF NOT EXISTS shelf_column_end INTEGER;
                    ALTER TABLE IF EXISTS serial_layouts
                        ADD COLUMN IF NOT EXISTS walls JSONB;
                    ALTER TABLE IF EXISTS serial_shelf_types
                        ADD COLUMN IF NOT EXISTS color VARCHAR;
                END$$;
                """
            )
        )
        return

    if dialect == "sqlite":
        inspector = inspect(db.get_bind())
        for table_name, required_columns in REQUIRED_COLUMNS.items():
            if not inspector.has_table(table_name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name in sorted(required_columns - existing):
                column_type = SQLITE_COLUMN_DEFINITIONS[(table_name, column_name)]
                db.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))


def check_schema_readiness(db: Session) -> dict:
    inspector = inspect(db.get_bind())
    missing_columns: list[str] = []
    for table_name, required_columns in REQUIRED_COLUMNS.items():
        if not inspector.has_table(table_name):
            missing_columns.extend(f"{table_name}.{column_name}" for column_name in sorted(required_columns))
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns.extend(
            f"{table_name}.{column_name}"
            for column_name in sorted(required_columns - existing)
        )

    missing_enum_values: list[str] = []
    if _dialect_name(db) == "postgresql":
        for enum_name, required_values in REQUIRED_ENUM_VALUES.items():
            existing = {
                row[0]
                for row in db.execute(
                    text(
                        """
                        SELECT e.enumlabel
                        FROM pg_type t
                        JOIN pg_enum e ON t.oid = e.enumtypid
                        WHERE t.typname = :enum_name
                        """
                    ),
                    {"enum_name": enum_name},
                ).all()
            }
            missing_enum_values.extend(
                f"{enum_name}.{value}"
                for value in sorted(required_values - existing)
            )

    backup_storage_writable = _check_backup_storage_writable()
    schema_status = "ok" if not missing_columns and not missing_enum_values else "missing"
    return {
        "schema_status": schema_status,
        "missing_columns": missing_columns,
        "missing_enum_values": missing_enum_values,
        "backup_storage_writable": backup_storage_writable,
    }


def _check_backup_storage_writable() -> bool:
    try:
        backup_dir = Path(get_settings().BACKUP_STORAGE_DIR)
        backup_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="health_", dir=backup_dir, delete=False) as tmp:
            tmp.write(b"ok")
            tmp_path = tmp.name
        os.unlink(tmp_path)
        return True
    except Exception:
        return False
