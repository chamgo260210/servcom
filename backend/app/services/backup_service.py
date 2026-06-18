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
    WORK_SCHEMA_VERSION,
    WORK_SYSTEM_SCHEMA_VERSION,
    WORK_SYSTEM_TABLES,
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
WORK_SYSTEM_USER_ROLES = {models.UserRole.OPERATOR, models.UserRole.MEMBER}
WORK_SYSTEM_AUDIT_ACTIONS = {
    "USER_CREATE",
    "USER_UPDATE",
    "USER_DELETE",
    "CREDENTIAL_UPDATE",
    "REQUEST_SUBMIT",
    "REQUEST_APPROVE",
    "REQUEST_REJECT",
    "REQUEST_CANCEL",
    "RESET_DATA",
}
WORK_SYSTEM_RESET_SCOPES = {"members", "operators_members"}


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
    if domain == "WORK_SYSTEM":
        return WORK_SYSTEM_TABLES
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
    if domain == "WORK_SYSTEM" and table_name == "users":
        rows = (
            db.query(models.User)
            .filter(models.User.role.in_(WORK_SYSTEM_USER_ROLES))
            .order_by(models.User.created_at.asc())
            .all()
        )
        return [_row_to_dict(row) for row in rows]
    if domain == "WORK_SYSTEM" and table_name == "auth_accounts":
        rows = (
            db.query(models.AuthAccount)
            .join(models.User, models.User.id == models.AuthAccount.user_id)
            .filter(models.User.role.in_(WORK_SYSTEM_USER_ROLES))
            .order_by(models.AuthAccount.user_id.asc())
            .all()
        )
        return [_row_to_dict(row) for row in rows]
    if domain == "WORK_SYSTEM" and table_name == "audit_logs":
        master_ids = {
            row[0] for row in db.query(models.User.id).filter(models.User.role == models.UserRole.MASTER).all()
        }
        rows = (
            db.query(models.AuditLog)
            .filter(models.AuditLog.action_type.in_(WORK_SYSTEM_AUDIT_ACTIONS))
            .order_by(models.AuditLog.created_at.asc())
            .all()
        )
        collected = []
        for row in rows:
            if row.actor_user_id in master_ids or row.target_user_id in master_ids:
                continue
            if row.action_type == "RESET_DATA":
                details = row.details if isinstance(row.details, dict) else {}
                if details.get("scope") not in WORK_SYSTEM_RESET_SCOPES:
                    continue
            collected.append(_row_to_dict(row))
        return collected
    if domain == "WORK" and table_name == "users":
        rows = (
            db.query(models.User)
            .filter(models.User.role == models.UserRole.MEMBER)
            .order_by(models.User.created_at.asc())
            .all()
        )
        return [_row_to_dict(row) for row in rows]
    if domain == "WORK" and table_name == "auth_accounts":
        rows = (
            db.query(models.AuthAccount)
            .join(models.User, models.User.id == models.AuthAccount.user_id)
            .filter(models.User.role == models.UserRole.MEMBER)
            .order_by(models.AuthAccount.user_id.asc())
            .all()
        )
        return [_row_to_dict(row) for row in rows]
    order_column = getattr(model, "created_at", None)
    if order_column is None:
        order_column = next(iter(model.__table__.primary_key.columns))
    rows = db.query(model).order_by(order_column.asc()).all()
    return [_row_to_dict(row) for row in rows]


def _storage_subdir(domain: str, kind: str) -> Path:
    kind = (kind or "MANUAL").strip().upper()
    if domain == "FULL":
        return Path("system") / "full"
    if domain == "WORK":
        return Path("work") / ("restore_points" if kind == "RESTORE_POINT" else "manual")
    if domain == "WORK_SYSTEM":
        return Path("work_system") / ("restore_points" if kind == "RESTORE_POINT" else "manual")
    if domain == "VISITORS":
        return Path("visitors") / ("restore_points" if kind == "RESTORE_POINT" else "manual")
    if domain == "SERIALS":
        return Path("serials") / ("restore_points" if kind == "RESTORE_POINT" else "manual")
    return Path(".")


def build_backup_payload(
    db: Session,
    *,
    domain: str,
    current_user: models.User,
    description: str | None = None,
    created_at: datetime | None = None,
    kind: str = "MANUAL",
) -> dict:
    domain = normalize_domain(domain)
    if domain not in BACKUP_DOMAINS:
        raise ValueError("unsupported_domain")
    created_at = created_at or datetime.now(timezone.utc)
    schema_version = WORK_SCHEMA_VERSION if domain == "WORK" else WORK_SYSTEM_SCHEMA_VERSION if domain == "WORK_SYSTEM" else SCHEMA_VERSION
    meta = {
        "backup_type": domain,
        "schema_version": schema_version,
        "created_at": created_at.isoformat(),
        "created_by": {
            "user_id": str(current_user.id),
            "name": current_user.name,
            "role": current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
        },
        "description": description,
        "kind": (kind or "MANUAL").strip().upper(),
    }
    if domain == "WORK":
        meta.update(
            {
                "work_policy": "member_accounts_v1",
                "includes_auth_accounts": True,
                "includes_audit_logs": False,
                "downloadable": False,
            }
        )
    if domain == "WORK_SYSTEM":
        meta.update(
            {
                "work_policy": "operator_member_accounts_v1",
                "includes_auth_accounts": True,
                "includes_audit_logs": True,
                "downloadable": False,
                "restorable": False,
            }
        )
    if domain == "FULL":
        meta.update(
            {
                "includes_audit_logs": True,
            }
        )
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
    kind: str = "MANUAL",
) -> models.DataBackup:
    domain = normalize_domain(domain)
    kind = (kind or "MANUAL").strip().upper()
    if domain not in BACKUP_DOMAINS:
        raise ValueError("unsupported_domain")

    created_at = datetime.now(timezone.utc)
    payload = build_backup_payload(
        db,
        domain=domain,
        current_user=current_user,
        description=description,
        created_at=created_at,
        kind=kind,
    )

    storage_dir = Path(get_settings().BACKUP_STORAGE_DIR) / _storage_subdir(domain, kind)
    storage_dir.mkdir(parents=True, exist_ok=True)
    stamp = created_at.strftime("%Y%m%d_%H%M%S")
    file_name = file_name or f"{domain.lower()}_backup_{stamp}.json"
    file_path = storage_dir / file_name
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=serialize_value), encoding="utf-8")

    backup = models.DataBackup(
        domain=domain,
        backup_type="JSON",
        kind=kind,
        file_name=file_name,
        file_path=str(file_path),
        file_size=file_path.stat().st_size,
        checksum=payload["checksum"]["value"],
        schema_version=WORK_SCHEMA_VERSION if domain == "WORK" else WORK_SYSTEM_SCHEMA_VERSION if domain == "WORK_SYSTEM" else SCHEMA_VERSION,
        status="READY",
        description=description,
        created_by=current_user.id,
    )
    db.add(backup)
    db.flush()
    return backup


def validate_work_system_backup_payload(payload: dict, *, expected_domain: str | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    meta = payload.get("meta") if isinstance(payload, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    checksum = payload.get("checksum") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        errors.append("meta object is required")
        meta = {}
    if not isinstance(data, dict):
        errors.append("data object is required")
        data = {}
    if not isinstance(checksum, dict):
        errors.append("checksum object is required")
        checksum = {}

    domain = normalize_domain(meta.get("backup_type") or meta.get("domain") or expected_domain or "")
    if domain != "WORK_SYSTEM":
        errors.append("Backup DB domain does not match WORK_SYSTEM")
    if expected_domain and normalize_domain(expected_domain) != domain:
        errors.append("Backup DB domain does not match backup file meta")
    if meta.get("schema_version") != WORK_SYSTEM_SCHEMA_VERSION:
        errors.append("Unsupported schema_version")

    required = set(WORK_SYSTEM_TABLES)
    actual = set(data)
    missing = required - actual
    unknown = actual - required
    if missing:
        errors.append(f"Missing table keys: {', '.join(sorted(missing))}")
    if unknown:
        errors.append(f"Unknown table keys: {', '.join(sorted(unknown))}")
    for table_name in required & actual:
        if not isinstance(data.get(table_name), list):
            errors.append(f"{table_name} must be a list")
            data[table_name] = []

    if isinstance(checksum, dict) and checksum.get("algorithm") != "sha256":
        errors.append("checksum algorithm must be sha256")
    expected_checksum = checksum.get("value") if isinstance(checksum, dict) else None
    if expected_checksum:
        actual_checksum = calculate_checksum(meta, data)
        if actual_checksum != expected_checksum:
            errors.append("checksum mismatch")
    else:
        errors.append("checksum value is required")

    user_ids = set()
    operator_ids = set()
    identifiers = set()
    login_ids = set()
    shift_ids = {str(row.get("id")) for row in data.get("shifts", []) if isinstance(row, dict) and row.get("id")}
    request_ids = {str(row.get("id")) for row in data.get("shift_requests", []) if isinstance(row, dict) and row.get("id")}

    for index, row in enumerate(data.get("users", [])):
        if not isinstance(row, dict):
            errors.append(f"users[{index}] must be an object")
            continue
        role = row.get("role")
        user_id = row.get("id")
        if role not in {models.UserRole.OPERATOR.value, models.UserRole.MEMBER.value}:
            errors.append(f"users[{index}].role must be OPERATOR or MEMBER")
        if not user_id:
            errors.append(f"users[{index}].id is required")
        else:
            user_id = str(user_id)
            if user_id in user_ids:
                errors.append(f"users[{index}].id is duplicated")
            user_ids.add(user_id)
            if role == models.UserRole.OPERATOR.value:
                operator_ids.add(user_id)
        identifier = row.get("identifier")
        if identifier:
            if identifier in identifiers:
                errors.append(f"users[{index}].identifier is duplicated")
            identifiers.add(identifier)

    for index, row in enumerate(data.get("auth_accounts", [])):
        if not isinstance(row, dict):
            errors.append(f"auth_accounts[{index}] must be an object")
            continue
        user_id = str(row.get("user_id")) if row.get("user_id") else ""
        login_id = row.get("login_id")
        if user_id not in user_ids:
            errors.append(f"auth_accounts[{index}].user_id references missing WORK_SYSTEM users")
        if not login_id:
            errors.append(f"auth_accounts[{index}].login_id is required")
        elif login_id in login_ids:
            errors.append(f"auth_accounts[{index}].login_id is duplicated")
        else:
            login_ids.add(login_id)
        if not row.get("password_hash"):
            errors.append(f"auth_accounts[{index}].password_hash is required")

    for index, row in enumerate(data.get("user_shifts", [])):
        if not isinstance(row, dict):
            errors.append(f"user_shifts[{index}] must be an object")
            continue
        if str(row.get("user_id")) not in user_ids:
            errors.append(f"user_shifts[{index}].user_id references missing WORK_SYSTEM users")
        if str(row.get("shift_id")) not in shift_ids:
            errors.append(f"user_shifts[{index}].shift_id references missing shifts")

    for index, row in enumerate(data.get("shift_requests", [])):
        if not isinstance(row, dict):
            errors.append(f"shift_requests[{index}] must be an object")
            continue
        if str(row.get("user_id")) not in user_ids:
            errors.append(f"shift_requests[{index}].user_id references missing WORK_SYSTEM users")
        if row.get("operator_id") and str(row.get("operator_id")) not in operator_ids:
            warnings.append(f"shift_requests[{index}].operator_id is outside WORK_SYSTEM operators and will be ignored on restore")
        if str(row.get("target_shift_id")) not in shift_ids:
            errors.append(f"shift_requests[{index}].target_shift_id references missing shifts")

    for index, row in enumerate(data.get("audit_logs", [])):
        if not isinstance(row, dict):
            errors.append(f"audit_logs[{index}] must be an object")
            continue
        action_type = row.get("action_type")
        if action_type not in WORK_SYSTEM_AUDIT_ACTIONS:
            errors.append(f"audit_logs[{index}].action_type is not allowed for WORK_SYSTEM")
        if action_type == "RESET_DATA":
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            if details.get("scope") not in WORK_SYSTEM_RESET_SCOPES:
                errors.append(f"audit_logs[{index}].details.scope is not allowed for WORK_SYSTEM RESET_DATA")
        if row.get("actor_user_id") and str(row.get("actor_user_id")) not in user_ids:
            warnings.append(f"audit_logs[{index}].actor_user_id is outside WORK_SYSTEM users")
        if row.get("target_user_id") and str(row.get("target_user_id")) not in user_ids:
            warnings.append(f"audit_logs[{index}].target_user_id is outside WORK_SYSTEM users")
        if row.get("request_id") and str(row.get("request_id")) not in request_ids:
            warnings.append(f"audit_logs[{index}].request_id is outside WORK_SYSTEM shift_requests")

    summary = {
        table_name: len(rows) if isinstance(rows, list) else 0
        for table_name, rows in data.items()
    }
    return {
        "valid": not errors,
        "domain": "WORK_SYSTEM" if domain == "WORK_SYSTEM" else None,
        "schema_version": meta.get("schema_version"),
        "summary": summary,
        "warnings": warnings,
        "errors": errors,
        "_payload": payload if not errors else None,
    }


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
