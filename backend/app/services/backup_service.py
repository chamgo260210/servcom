from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from ..config import get_settings
from ..core.backup_manifest import (
    SCHEMA_VERSION,
    SERIAL_TABLES,
    SUPPORTED_DOMAINS,
    VISITOR_TABLES,
    calculate_checksum,
    serialize_value,
)


TABLE_MODEL_MAP = {
    "visitor_school_years": models.VisitorSchoolYear,
    "visitor_periods": models.VisitorPeriod,
    "visitor_daily_counts": models.VisitorDailyCount,
    "visitor_running_totals": models.VisitorRunningTotal,
    "visitor_monthly_stats": models.VisitorMonthlyStat,
    "visitor_period_stats": models.VisitorPeriodStat,
    "visitor_year_stats": models.VisitorYearStat,
    "serial_layouts": models.SerialLayout,
    "serial_shelf_types": models.SerialShelfType,
    "serial_shelves": models.SerialShelf,
    "serial_publications": models.SerialPublication,
}


def normalize_domain(domain: str) -> str:
    return (domain or "").strip().upper()


def _backup_tables_for_domain(domain: str) -> list[str]:
    if domain == "VISITORS":
        return VISITOR_TABLES
    if domain == "SERIALS":
        return SERIAL_TABLES
    raise ValueError("unsupported_domain")


def _row_to_dict(row) -> dict:
    return {column.name: serialize_value(getattr(row, column.name)) for column in row.__table__.columns}


def _collect_table_data(db: Session, table_name: str) -> list[dict]:
    model = TABLE_MODEL_MAP[table_name]
    order_column = getattr(model, "created_at", None)
    if order_column is None:
        order_column = next(iter(model.__table__.primary_key.columns))
    rows = db.query(model).order_by(order_column.asc()).all()
    return [_row_to_dict(row) for row in rows]


def create_json_backup(
    db: Session,
    *,
    domain: str,
    current_user: models.User,
    description: str | None = None,
    file_name: str | None = None,
) -> models.DataBackup:
    domain = normalize_domain(domain)
    if domain not in SUPPORTED_DOMAINS:
        raise ValueError("unsupported_domain")

    created_at = datetime.now(timezone.utc)
    meta = {
        "backup_type": domain,
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at.isoformat(),
        "created_by": {
            "user_id": str(current_user.id),
            "name": current_user.name,
            "role": current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
        },
        "description": description,
    }
    data = {table_name: _collect_table_data(db, table_name) for table_name in _backup_tables_for_domain(domain)}
    checksum = calculate_checksum(meta, data)
    payload = {
        "meta": meta,
        "data": data,
        "checksum": {
            "algorithm": "sha256",
            "value": checksum,
        },
    }

    storage_dir = Path(get_settings().BACKUP_STORAGE_DIR)
    storage_dir.mkdir(parents=True, exist_ok=True)
    stamp = created_at.strftime("%Y%m%d_%H%M%S")
    file_name = file_name or f"{domain.lower()}_backup_{stamp}.json"
    file_path = storage_dir / file_name
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=serialize_value), encoding="utf-8")

    backup = models.DataBackup(
        domain=domain,
        backup_type="JSON",
        file_name=file_name,
        file_path=str(file_path),
        file_size=file_path.stat().st_size,
        checksum=checksum,
        schema_version=SCHEMA_VERSION,
        status="READY",
        description=description,
        created_by=current_user.id,
    )
    db.add(backup)
    db.flush()
    return backup
