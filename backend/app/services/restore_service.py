from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import Date, DateTime, Enum as SqlEnum, Time
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session

from .. import models
from ..config import get_settings
from ..core.backup_manifest import (
    SCHEMA_VERSION,
    SERIAL_TABLES,
    SUPPORTED_DOMAINS,
    VISITOR_TABLES,
    calculate_checksum,
)
from .backup_service import TABLE_MODEL_MAP, create_json_backup, normalize_domain

VISITOR_DELETE_ORDER = [
    "visitor_year_stats",
    "visitor_period_stats",
    "visitor_monthly_stats",
    "visitor_running_totals",
    "visitor_daily_counts",
    "visitor_periods",
    "visitor_school_years",
]
VISITOR_INSERT_ORDER = [
    "visitor_school_years",
    "visitor_periods",
    "visitor_daily_counts",
    "visitor_running_totals",
    "visitor_monthly_stats",
    "visitor_period_stats",
    "visitor_year_stats",
]
SERIAL_DELETE_ORDER = [
    "serial_publications",
    "serial_shelves",
    "serial_shelf_types",
    "serial_layouts",
]
SERIAL_INSERT_ORDER = [
    "serial_layouts",
    "serial_shelf_types",
    "serial_shelves",
    "serial_publications",
]

USER_REFERENCE_COLUMNS = {"created_by", "updated_by"}


def _required_tables(domain: str) -> list[str]:
    if domain == "VISITORS":
        return VISITOR_TABLES
    if domain == "SERIALS":
        return SERIAL_TABLES
    return []


def _insert_order(domain: str) -> list[str]:
    return VISITOR_INSERT_ORDER if domain == "VISITORS" else SERIAL_INSERT_ORDER


def _delete_order(domain: str) -> list[str]:
    return VISITOR_DELETE_ORDER if domain == "VISITORS" else SERIAL_DELETE_ORDER


def _safe_backup_path(backup: models.DataBackup) -> Path:
    storage_root = Path(get_settings().BACKUP_STORAGE_DIR).resolve()
    file_path = Path(backup.file_path).resolve()
    if file_path != storage_root and storage_root not in file_path.parents:
        raise PermissionError("Backup file is outside storage directory")
    return file_path


def _load_payload(backup: models.DataBackup) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    try:
        file_path = _safe_backup_path(backup)
    except PermissionError as exc:
        return None, [str(exc)]
    if not file_path.is_file():
        return None, ["Backup file not found"]
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"Invalid JSON: {exc.msg}"]
    except OSError as exc:
        return None, [f"Unable to read backup file: {exc}"]
    if not isinstance(payload, dict):
        errors.append("Backup root must be an object")
        return None, errors
    return payload, errors


def parse_backup_json_bytes(content: bytes) -> tuple[dict | None, list[str]]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except UnicodeDecodeError:
        return None, ["Backup file must be UTF-8 encoded JSON"]
    except json.JSONDecodeError as exc:
        return None, [f"Invalid JSON: {exc.msg}"]
    if not isinstance(payload, dict):
        return None, ["Backup root must be an object"]
    return payload, []


def _validate_checksum(payload: dict, errors: list[str]) -> None:
    checksum = payload.get("checksum")
    meta = payload.get("meta")
    data = payload.get("data")
    if not isinstance(checksum, dict):
        errors.append("checksum object is required")
        return
    if checksum.get("algorithm") != "sha256":
        errors.append("checksum algorithm must be sha256")
        return
    expected = checksum.get("value")
    if not expected:
        errors.append("checksum value is required")
        return
    actual = calculate_checksum(meta, data)
    if actual != expected:
        errors.append("checksum mismatch")


def _parse_uuid(value, label: str, errors: list[str]) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        errors.append(f"{label} must be a valid UUID")
        return None


def _parse_temporal(value, column_type, label: str, errors: list[str]):
    if value in (None, ""):
        return None
    try:
        if isinstance(column_type, DateTime):
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if isinstance(column_type, Date):
            return date.fromisoformat(str(value))
        if isinstance(column_type, Time):
            return time.fromisoformat(str(value))
    except ValueError:
        errors.append(f"{label} must be a valid ISO value")
        return None
    return value


def _coerce_row(table_name: str, row: dict, errors: list[str], *, row_index: int) -> dict:
    model = TABLE_MODEL_MAP[table_name]
    columns = {column.name: column for column in model.__table__.columns}
    unknown_columns = sorted(set(row) - set(columns))
    if unknown_columns:
        errors.append(f"{table_name}[{row_index}] has unknown columns: {', '.join(unknown_columns)}")
    coerced = {}
    for name, column in columns.items():
        value = row.get(name)
        label = f"{table_name}[{row_index}].{name}"
        column_type = column.type
        if isinstance(column_type, PgUUID):
            coerced[name] = _parse_uuid(value, label, errors)
        elif isinstance(column_type, SqlEnum):
            enum_class = column_type.enum_class
            if value in (None, ""):
                coerced[name] = None
            elif enum_class:
                try:
                    coerced[name] = enum_class(value)
                except ValueError:
                    errors.append(f"{label} has invalid enum value")
                    coerced[name] = None
            else:
                allowed = set(column_type.enums or [])
                if value not in allowed:
                    errors.append(f"{label} has invalid enum value")
                coerced[name] = value
        elif isinstance(column_type, (DateTime, Date, Time)):
            coerced[name] = _parse_temporal(value, column_type, label, errors)
        else:
            coerced[name] = value
    return coerced


def _coerce_data(data: dict, domain: str, errors: list[str]) -> dict[str, list[dict]]:
    coerced: dict[str, list[dict]] = {}
    for table_name in _required_tables(domain):
        table_rows = data.get(table_name)
        if not isinstance(table_rows, list):
            errors.append(f"{table_name} must be a list")
            coerced[table_name] = []
            continue
        coerced[table_name] = [
            _coerce_row(table_name, row if isinstance(row, dict) else {}, errors, row_index=index)
            for index, row in enumerate(table_rows)
        ]
        for index, row in enumerate(table_rows):
            if not isinstance(row, dict):
                errors.append(f"{table_name}[{index}] must be an object")
    return coerced


def _validate_internal_foreign_keys(domain: str, data: dict[str, list[dict]], errors: list[str]) -> None:
    if domain == "VISITORS":
        year_ids = {row.get("id") for row in data["visitor_school_years"] if row.get("id")}
        period_ids = {row.get("id") for row in data["visitor_periods"] if row.get("id")}
        for table_name in [
            "visitor_periods",
            "visitor_daily_counts",
            "visitor_running_totals",
            "visitor_monthly_stats",
            "visitor_period_stats",
            "visitor_year_stats",
        ]:
            for index, row in enumerate(data[table_name]):
                if row.get("school_year_id") not in year_ids:
                    errors.append(f"{table_name}[{index}].school_year_id references missing visitor_school_years")
        for index, row in enumerate(data["visitor_period_stats"]):
            if row.get("period_id") not in period_ids:
                errors.append(f"visitor_period_stats[{index}].period_id references missing visitor_periods")
    elif domain == "SERIALS":
        layout_ids = {row.get("id") for row in data["serial_layouts"] if row.get("id")}
        shelf_type_ids = {row.get("id") for row in data["serial_shelf_types"] if row.get("id")}
        shelf_ids = {row.get("id") for row in data["serial_shelves"] if row.get("id")}
        for index, row in enumerate(data["serial_shelves"]):
            if row.get("layout_id") not in layout_ids:
                errors.append(f"serial_shelves[{index}].layout_id references missing serial_layouts")
            if row.get("shelf_type_id") not in shelf_type_ids:
                errors.append(f"serial_shelves[{index}].shelf_type_id references missing serial_shelf_types")
        for index, row in enumerate(data["serial_publications"]):
            shelf_id = row.get("shelf_id")
            if shelf_id is not None and shelf_id not in shelf_ids:
                errors.append(f"serial_publications[{index}].shelf_id references missing serial_shelves")


def _validate_user_foreign_keys(db: Session, data: dict[str, list[dict]], errors: list[str]) -> None:
    referenced_user_ids = set()
    for table_rows in data.values():
        for row in table_rows:
            for column_name in USER_REFERENCE_COLUMNS:
                if row.get(column_name):
                    referenced_user_ids.add(row[column_name])
    if not referenced_user_ids:
        return
    existing = {row[0] for row in db.query(models.User.id).filter(models.User.id.in_(referenced_user_ids)).all()}
    missing = referenced_user_ids - existing
    if missing:
        errors.append(f"Backup references missing users: {', '.join(sorted(str(item) for item in missing))}")


def validate_backup_payload(
    db: Session,
    payload: dict,
    *,
    expected_domain: str | None = None,
    backup_type: str = "JSON",
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    meta = payload.get("meta")
    data = payload.get("data")
    checksum = payload.get("checksum")
    if not isinstance(meta, dict):
        errors.append("meta object is required")
        meta = {}
    if not isinstance(data, dict):
        errors.append("data object is required")
        data = {}
    if not isinstance(checksum, dict):
        errors.append("checksum object is required")

    backup_type_domain = normalize_domain(meta.get("backup_type") or "")
    meta_domain = normalize_domain(meta.get("domain") or "")
    domain = backup_type_domain or meta_domain or normalize_domain(expected_domain or "")
    schema_version = meta.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        errors.append("Unsupported schema_version")
    if domain not in SUPPORTED_DOMAINS:
        errors.append("Unsupported backup_type/domain")
    if meta_domain and meta_domain not in SUPPORTED_DOMAINS:
        errors.append("Unsupported meta.domain")
    if backup_type_domain and meta_domain and backup_type_domain != meta_domain:
        errors.append("meta.backup_type and meta.domain do not match")
    if expected_domain and normalize_domain(expected_domain) != domain:
        errors.append("Backup DB domain does not match backup file meta")
    if backup_type != "JSON":
        errors.append("Only JSON backups can be restored")
    if isinstance(data, dict) and ({"users", "auth_accounts"} & set(data.keys())):
        errors.append("users/auth_accounts are not allowed in upload restore")

    if isinstance(data, dict) and domain in SUPPORTED_DOMAINS:
        required = set(_required_tables(domain))
        actual = set(data.keys())
        missing = required - actual
        unknown = actual - required
        if missing:
            errors.append(f"Missing table keys: {', '.join(sorted(missing))}")
        if unknown:
            errors.append(f"Unknown table keys: {', '.join(sorted(unknown))}")
        coerced = _coerce_data(data, domain, errors) if not missing else {name: [] for name in required}
        if not missing and not unknown:
            _validate_internal_foreign_keys(domain, coerced, errors)
            _validate_user_foreign_keys(db, coerced, errors)
    else:
        coerced = {}

    if isinstance(meta, dict) and isinstance(data, dict) and isinstance(checksum, dict):
        _validate_checksum(payload, errors)

    summary = {table_name: len(rows) for table_name, rows in data.items()} if isinstance(data, dict) else {}
    return {
        "valid": not errors,
        "domain": domain if domain in SUPPORTED_DOMAINS else None,
        "schema_version": schema_version,
        "summary": summary,
        "warnings": warnings,
        "errors": errors,
        "_payload": payload if not errors else None,
        "_coerced_data": coerced if not errors else None,
    }


def validate_backup_file(db: Session, backup: models.DataBackup) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    payload, load_errors = _load_payload(backup)
    errors.extend(load_errors)
    if payload is None:
        return {"valid": False, "domain": None, "schema_version": None, "summary": {}, "warnings": warnings, "errors": errors}
    result = validate_backup_payload(
        db,
        payload,
        expected_domain=backup.domain,
        backup_type=backup.backup_type,
    )
    result["errors"] = errors + result.get("errors", [])
    result["valid"] = not result["errors"]
    return result


def _insert_rows(db: Session, domain: str, data: dict[str, list[dict]]) -> None:
    for table_name in _insert_order(domain):
        model = TABLE_MODEL_MAP[table_name]
        for row in data[table_name]:
            db.add(model(**row))


def _delete_domain_rows(db: Session, domain: str) -> None:
    for table_name in _delete_order(domain):
        db.query(TABLE_MODEL_MAP[table_name]).delete(synchronize_session=False)


def restore_backup(
    db: Session,
    *,
    backup: models.DataBackup,
    current_user: models.User,
    mode: str,
) -> tuple[models.DataRestoreJob, dict]:
    mode = (mode or "").strip().upper()
    validation = validate_backup_file(db, backup)
    if not validation["valid"]:
        job = models.DataRestoreJob(
            backup_id=backup.id,
            domain=normalize_domain(backup.domain),
            mode=mode or "REPLACE",
            status="FAILED",
            requested_by=current_user.id,
            finished_at=datetime.now(timezone.utc),
            error_message="Backup validation failed",
            summary={"errors": validation["errors"]},
        )
        db.add(job)
        db.flush()
        return job, validation

    if mode == "DRY_RUN":
        job = models.DataRestoreJob(
            backup_id=backup.id,
            domain=validation["domain"],
            mode="DRY_RUN",
            status="SUCCESS",
            requested_by=current_user.id,
            finished_at=datetime.now(timezone.utc),
            summary={"validation": validation["summary"]},
        )
        db.add(job)
        db.flush()
        return job, validation
    if mode != "REPLACE":
        raise ValueError("unsupported_restore_mode")

    domain = validation["domain"]
    job = models.DataRestoreJob(
        backup_id=backup.id,
        domain=domain,
        mode="REPLACE",
        status="PENDING",
        requested_by=current_user.id,
    )
    db.add(job)
    db.flush()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pre_restore = create_json_backup(
        db,
        domain=domain,
        current_user=current_user,
        description=f"Automatic pre-restore backup for restore job {job.id}",
        file_name=f"pre_restore_{stamp}_{domain.lower()}.json",
    )
    _delete_domain_rows(db, domain)
    _insert_rows(db, domain, validation["_coerced_data"])
    job.status = "SUCCESS"
    job.finished_at = datetime.now(timezone.utc)
    job.summary = {
        "restored": validation["summary"],
        "pre_restore_backup_id": str(pre_restore.id),
    }
    return job, validation


def restore_uploaded_backup(
    db: Session,
    *,
    payload: dict,
    current_user: models.User,
    mode: str = "REPLACE",
) -> tuple[models.DataRestoreJob, dict]:
    mode = (mode or "").strip().upper()
    validation = validate_backup_payload(db, payload, backup_type="JSON")
    domain = validation["domain"] or "UNKNOWN"
    if not validation["valid"]:
        job = models.DataRestoreJob(
            backup_id=None,
            domain=domain,
            mode=mode or "REPLACE",
            status="FAILED",
            requested_by=current_user.id,
            finished_at=datetime.now(timezone.utc),
            error_message="Uploaded backup validation failed",
            summary={"errors": validation["errors"]},
        )
        db.add(job)
        db.flush()
        return job, validation

    if mode != "REPLACE":
        raise ValueError("unsupported_restore_mode")

    job = models.DataRestoreJob(
        backup_id=None,
        domain=domain,
        mode="REPLACE",
        status="PENDING",
        requested_by=current_user.id,
    )
    db.add(job)
    db.flush()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pre_restore = create_json_backup(
        db,
        domain=domain,
        current_user=current_user,
        description=f"Automatic pre-upload-restore backup for restore job {job.id}",
        file_name=f"pre_restore_{stamp}_{domain.lower()}.json",
    )
    _delete_domain_rows(db, domain)
    _insert_rows(db, domain, validation["_coerced_data"])
    job.status = "SUCCESS"
    job.finished_at = datetime.now(timezone.utc)
    job.summary = {
        "restored": validation["summary"],
        "pre_restore_backup_id": str(pre_restore.id),
        "source": "UPLOAD",
    }
    return job, validation
