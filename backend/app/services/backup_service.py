from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from ..config import get_settings
from ..core.backup_manifest import (
    BACKUP_DOMAINS,
    FULL_TABLES,
    SCHEMA_VERSION,
    SERIAL_TABLES,
    SUPPORTED_DOMAINS,
    VISITOR_TABLES,
    WORK_TABLES,
    calculate_checksum,
    serialize_value,
)


TABLE_MODEL_MAP = {
    "users": models.User,
    "auth_accounts": models.AuthAccount,
    "shifts": models.Shift,
    "user_shifts": models.UserShift,
    "shift_requests": models.ShiftRequest,
    "audit_logs": models.AuditLog,
    "notices": models.Notice,
    "notice_targets": models.NoticeTarget,
    "notice_reads": models.NoticeRead,
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

WORK_USER_COLUMNS = {"id", "name", "identifier", "role", "active"}


def normalize_domain(domain: str) -> str:
    return (domain or "").strip().upper()


def _backup_tables_for_domain(domain: str) -> list[str]:
    if domain == "VISITORS":
        return VISITOR_TABLES
    if domain == "SERIALS":
        return SERIAL_TABLES
    if domain == "FULL":
        return FULL_TABLES
    if domain == "WORK":
        return WORK_TABLES
    raise ValueError("unsupported_domain")


def _row_to_dict(row, *, include_columns: set[str] | None = None) -> dict:
    values = {}
    for column in row.__table__.columns:
        if include_columns is not None and column.name not in include_columns:
            continue
        values[column.name] = serialize_value(getattr(row, column.name))
    return values


def _collect_table_data(db: Session, table_name: str, *, domain: str) -> list[dict]:
    model = TABLE_MODEL_MAP[table_name]
    order_column = getattr(model, "created_at", None)
    if order_column is None:
        order_column = next(iter(model.__table__.primary_key.columns))
    rows = db.query(model).order_by(order_column.asc()).all()
    include_columns = WORK_USER_COLUMNS if domain == "WORK" and table_name == "users" else None
    return [_row_to_dict(row, include_columns=include_columns) for row in rows]


def build_backup_payload(
    db: Session,
    *,
    domain: str,
    current_user: models.User,
    description: str | None = None,
    created_at: datetime | None = None,
) -> dict:
    domain = normalize_domain(domain)
    if domain not in BACKUP_DOMAINS:
        raise ValueError("unsupported_domain")
    created_at = created_at or datetime.now(timezone.utc)
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
    data = {table_name: _collect_table_data(db, table_name, domain=domain) for table_name in _backup_tables_for_domain(domain)}
    checksum = calculate_checksum(meta, data)
    return {
        "meta": meta,
        "data": data,
        "checksum": {
            "algorithm": "sha256",
            "value": checksum,
        },
    }


def create_json_backup(
    db: Session,
    *,
    domain: str,
    current_user: models.User,
    description: str | None = None,
    file_name: str | None = None,
) -> models.DataBackup:
    domain = normalize_domain(domain)
    if domain not in BACKUP_DOMAINS:
        raise ValueError("unsupported_domain")

    created_at = datetime.now(timezone.utc)
    payload = build_backup_payload(db, domain=domain, current_user=current_user, description=description, created_at=created_at)

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
        checksum=payload["checksum"]["value"],
        schema_version=SCHEMA_VERSION,
        status="READY",
        description=description,
        created_by=current_user.id,
    )
    db.add(backup)
    db.flush()
    return backup


def build_sanitized_backup_payload(file_path: Path) -> dict:
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(meta, dict) or not isinstance(data, dict):
        raise ValueError("invalid_backup_payload")
    if normalize_domain(meta.get("backup_type") or "") == "FULL":
        data = {key: value for key, value in data.items() if key != "auth_accounts"}
        meta = {**meta, "sanitized": True}
    checksum = calculate_checksum(meta, data)
    return {
        "meta": meta,
        "data": data,
        "checksum": {
            "algorithm": "sha256",
            "value": checksum,
        },
    }


def get_expired_backups(db: Session, domain: str | None = None) -> list[models.DataBackup]:
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.BACKUP_RETENTION_DAYS)
    query = db.query(models.DataBackup).filter(models.DataBackup.deleted_at.is_(None))
    if domain:
        query = query.filter(models.DataBackup.domain == normalize_domain(domain))

    expired_by_age = query.filter(models.DataBackup.created_at < cutoff).all()
    expired_ids = {backup.id for backup in expired_by_age}

    domains = [normalize_domain(domain)] if domain else [row[0] for row in db.query(models.DataBackup.domain).distinct().all()]
    for domain_name in domains:
        rows = (
            db.query(models.DataBackup)
            .filter(models.DataBackup.deleted_at.is_(None), models.DataBackup.domain == domain_name)
            .order_by(models.DataBackup.created_at.desc())
            .all()
        )
        for backup in rows[settings.BACKUP_MAX_FILES_PER_DOMAIN:]:
            expired_ids.add(backup.id)

    if not expired_ids:
        return []
    return db.query(models.DataBackup).filter(models.DataBackup.id.in_(expired_ids)).all()
