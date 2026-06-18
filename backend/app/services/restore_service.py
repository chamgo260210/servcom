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
    BACKUP_DOMAINS,
    FULL_TABLES,
    SCHEMA_VERSION,
    SERIAL_TABLES,
    SERVER_RESTORE_DOMAINS,
    SUPPORTED_DOMAINS,
    SUPPORTED_SCHEMA_VERSIONS,
    UPLOAD_RESTORE_DOMAINS,
    VISITOR_TABLES,
    WORK_SYSTEM_TABLES,
    WORK_TABLES,
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
WORK_DELETE_ORDER = [
    "shift_requests",
    "user_shifts",
    "shifts",
]
WORK_INSERT_ORDER = [
    "shifts",
    "user_shifts",
    "shift_requests",
]
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
FULL_DELETE_ORDER = [
    "notice_reads",
    "notice_targets",
    "notices",
    *VISITOR_DELETE_ORDER,
    *SERIAL_DELETE_ORDER,
    "shift_requests",
    "user_shifts",
    "auth_accounts",
    "shifts",
    "users",
]
FULL_INSERT_ORDER = [
    "users",
    "auth_accounts",
    "shifts",
    "user_shifts",
    "shift_requests",
    "notices",
    "notice_targets",
    "notice_reads",
    *VISITOR_INSERT_ORDER,
    *SERIAL_INSERT_ORDER,
]

USER_REFERENCE_COLUMNS = {"created_by", "updated_by"}


def _required_tables(domain: str) -> list[str]:
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
    return []


def _insert_order(domain: str) -> list[str]:
    if domain == "VISITORS":
        return VISITOR_INSERT_ORDER
    if domain == "SERIALS":
        return SERIAL_INSERT_ORDER
    if domain == "FULL":
        return FULL_INSERT_ORDER
    if domain == "WORK":
        return WORK_INSERT_ORDER
    return []


def _delete_order(domain: str) -> list[str]:
    if domain == "VISITORS":
        return VISITOR_DELETE_ORDER
    if domain == "SERIALS":
        return SERIAL_DELETE_ORDER
    if domain == "FULL":
        return FULL_DELETE_ORDER
    if domain == "WORK":
        return WORK_DELETE_ORDER
    return []


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


def _validate_user_foreign_keys(
    db: Session,
    data: dict[str, list[dict]],
    errors: list[str],
    *,
    allowed_user_ids: set[UUID] | None = None,
) -> None:
    referenced_user_ids = set()
    for table_rows in data.values():
        for row in table_rows:
            for column_name in USER_REFERENCE_COLUMNS:
                if row.get(column_name):
                    referenced_user_ids.add(row[column_name])
    if not referenced_user_ids:
        return
    existing = allowed_user_ids
    if existing is None:
        existing = {row[0] for row in db.query(models.User.id).filter(models.User.id.in_(referenced_user_ids)).all()}
    missing = referenced_user_ids - existing
    if missing:
        errors.append(f"Backup references missing users: {', '.join(sorted(str(item) for item in missing))}")


def _role_value(value) -> str:
    if hasattr(value, "value"):
        return value.value
    return str(value) if value is not None else ""


def _validate_work_foreign_keys(
    db: Session,
    data: dict[str, list[dict]],
    errors: list[str],
    warnings: list[str],
) -> None:
    current_users = {row.id: row for row in db.query(models.User).all()}
    current_user_ids = set(current_users)
    backup_member_ids = set()
    member_identifiers: dict[str, int] = {}
    duplicate_member_identifiers: set[str] = set()
    ignored_non_member_count = 0
    for index, row in enumerate(data["users"]):
        role = _role_value(row.get("role"))
        user_id = row.get("id")
        identifier = row.get("identifier")
        if role == models.UserRole.MEMBER.value:
            if user_id in current_users and current_users[user_id].role != models.UserRole.MEMBER:
                errors.append(f"users[{index}].id conflicts with existing non-MEMBER user")
            else:
                backup_member_ids.add(user_id)
            if identifier:
                if identifier in member_identifiers:
                    duplicate_member_identifiers.add(identifier)
                else:
                    member_identifiers[identifier] = index
                identifier_conflict = db.query(models.User).filter(
                    models.User.identifier == identifier,
                    models.User.id != user_id,
                ).first()
                if identifier_conflict:
                    errors.append(f"users[{index}].identifier conflicts with existing user")
        else:
            ignored_non_member_count += 1
    for identifier in sorted(duplicate_member_identifiers):
        errors.append(f"WORK backup contains duplicate MEMBER users.identifier: {identifier}")
    if ignored_non_member_count:
        warnings.append(f"WORK backup contains {ignored_non_member_count} non-MEMBER users; they will be ignored")

    existing_member_ids = {
        row[0] for row in db.query(models.User.id).filter(models.User.role == models.UserRole.MEMBER).all()
    }
    inactive_count = len(existing_member_ids - backup_member_ids)
    if inactive_count:
        warnings.append(f"{inactive_count} existing MEMBER users are not in the backup and will be set active=false")

    if "auth_accounts" not in data:
        data["auth_accounts"] = []
    if not data["auth_accounts"]:
        warnings.append("WORK backup has no auth_accounts; MEMBER account restore will be skipped")
    auth_login_ids: dict[str, int] = {}
    duplicate_auth_login_ids: set[str] = set()
    for index, row in enumerate(data["auth_accounts"]):
        user_id = row.get("user_id")
        login_id = row.get("login_id")
        password_hash = row.get("password_hash")
        if user_id not in backup_member_ids:
            errors.append(f"auth_accounts[{index}].user_id references missing WORK MEMBER users")
        if not login_id:
            errors.append(f"auth_accounts[{index}].login_id is required")
        if not password_hash:
            errors.append(f"auth_accounts[{index}].password_hash is required")
        if login_id:
            if login_id in auth_login_ids:
                duplicate_auth_login_ids.add(login_id)
            else:
                auth_login_ids[login_id] = index
            conflict = db.query(models.AuthAccount).filter(models.AuthAccount.login_id == login_id).first()
            if conflict and conflict.user_id != user_id:
                conflict_user = current_users.get(conflict.user_id)
                role_label = _role_value(conflict_user.role) if conflict_user else "UNKNOWN"
                errors.append(f"auth_accounts[{index}].login_id conflicts with existing {role_label} user")
    for login_id in sorted(duplicate_auth_login_ids):
        errors.append(f"WORK backup contains duplicate auth_accounts.login_id: {login_id}")

    shift_ids = {row.get("id") for row in data["shifts"] if row.get("id")}
    for index, row in enumerate(data["user_shifts"]):
        if row.get("user_id") not in backup_member_ids:
            errors.append(f"user_shifts[{index}].user_id references missing WORK MEMBER users")
        if row.get("shift_id") not in shift_ids:
            errors.append(f"user_shifts[{index}].shift_id references missing shifts")
    for index, row in enumerate(data["shift_requests"]):
        if row.get("user_id") not in backup_member_ids:
            errors.append(f"shift_requests[{index}].user_id references missing WORK MEMBER users")
        if row.get("operator_id") and row.get("operator_id") not in current_user_ids:
            warnings.append(f"shift_requests[{index}].operator_id references missing users and will be restored as NULL")
        if row.get("target_shift_id") not in shift_ids:
            errors.append(f"shift_requests[{index}].target_shift_id references missing shifts")


def _validate_work_system_foreign_keys(
    db: Session,
    data: dict[str, list[dict]],
    errors: list[str],
    warnings: list[str],
) -> None:
    current_users = {row.id: row for row in db.query(models.User).all()}
    current_auth_accounts = {row.user_id: row for row in db.query(models.AuthAccount).all()}
    master_user_ids = {
        user_id for user_id, user in current_users.items()
        if user.role == models.UserRole.MASTER
    }
    master_login_ids = {
        account.login_id for user_id, account in current_auth_accounts.items()
        if user_id in master_user_ids
    }

    backup_user_ids: set[UUID] = set()
    backup_operator_ids: set[UUID] = set()
    duplicate_user_ids: set[UUID] = set()
    identifiers: dict[str, UUID] = {}
    duplicate_identifiers: set[str] = set()
    for index, row in enumerate(data["users"]):
        role = _role_value(row.get("role"))
        user_id = row.get("id")
        identifier = row.get("identifier")
        if role not in {models.UserRole.OPERATOR.value, models.UserRole.MEMBER.value}:
            errors.append(f"users[{index}].role must be OPERATOR or MEMBER")
        if not user_id:
            errors.append(f"users[{index}].id is required")
            continue
        if user_id in master_user_ids:
            errors.append(f"users[{index}].id conflicts with existing MASTER user")
            continue
        existing = current_users.get(user_id)
        if existing and existing.role == models.UserRole.MASTER:
            errors.append(f"users[{index}].id conflicts with existing MASTER user")
            continue
        if user_id in backup_user_ids:
            duplicate_user_ids.add(user_id)
        backup_user_ids.add(user_id)
        if role == models.UserRole.OPERATOR.value:
            backup_operator_ids.add(user_id)
        if identifier:
            if identifier in identifiers and identifiers[identifier] != user_id:
                duplicate_identifiers.add(identifier)
            else:
                identifiers[identifier] = user_id
            conflict = db.query(models.User).filter(
                models.User.identifier == identifier,
                models.User.id != user_id,
            ).first()
            if conflict:
                errors.append(f"users[{index}].identifier conflicts with existing user")
    for user_id in sorted(duplicate_user_ids, key=str):
        errors.append(f"WORK_SYSTEM backup contains duplicate users.id: {user_id}")
    for identifier in sorted(duplicate_identifiers):
        errors.append(f"WORK_SYSTEM backup contains duplicate users.identifier: {identifier}")

    existing_work_user_ids = {
        row[0] for row in db.query(models.User.id).filter(
            models.User.role.in_((models.UserRole.OPERATOR, models.UserRole.MEMBER))
        ).all()
    }
    inactive_count = len(existing_work_user_ids - backup_user_ids)
    if inactive_count:
        warnings.append(f"{inactive_count} existing OPERATOR/MEMBER users are not in the backup and will be set active=false")

    login_ids: dict[str, UUID] = {}
    auth_user_ids: set[UUID] = set()
    duplicate_auth_user_ids: set[UUID] = set()
    duplicate_login_ids: set[str] = set()
    for index, row in enumerate(data["auth_accounts"]):
        user_id = row.get("user_id")
        login_id = row.get("login_id")
        password_hash = row.get("password_hash")
        if user_id not in backup_user_ids:
            errors.append(f"auth_accounts[{index}].user_id references missing WORK_SYSTEM users")
        if user_id:
            if user_id in auth_user_ids:
                duplicate_auth_user_ids.add(user_id)
            auth_user_ids.add(user_id)
        if not login_id:
            errors.append(f"auth_accounts[{index}].login_id is required")
        if not password_hash:
            errors.append(f"auth_accounts[{index}].password_hash is required")
        if login_id:
            if login_id in login_ids and login_ids[login_id] != user_id:
                duplicate_login_ids.add(login_id)
            else:
                login_ids[login_id] = user_id
            if login_id in master_login_ids:
                errors.append(f"auth_accounts[{index}].login_id conflicts with existing MASTER auth_account")
            conflict = db.query(models.AuthAccount).filter(
                models.AuthAccount.login_id == login_id,
                models.AuthAccount.user_id != user_id,
            ).first()
            if conflict:
                conflict_user = current_users.get(conflict.user_id)
                role_label = _role_value(conflict_user.role) if conflict_user else "UNKNOWN"
                errors.append(f"auth_accounts[{index}].login_id conflicts with existing {role_label} user")
    for login_id in sorted(duplicate_login_ids):
        errors.append(f"WORK_SYSTEM backup contains duplicate auth_accounts.login_id: {login_id}")
    for user_id in sorted(duplicate_auth_user_ids, key=str):
        errors.append(f"WORK_SYSTEM backup contains duplicate auth_accounts.user_id: {user_id}")

    shift_ids: set[UUID] = set()
    duplicate_shift_ids: set[UUID] = set()
    for row in data["shifts"]:
        shift_id = row.get("id")
        if not shift_id:
            continue
        if shift_id in shift_ids:
            duplicate_shift_ids.add(shift_id)
        shift_ids.add(shift_id)
    for shift_id in sorted(duplicate_shift_ids, key=str):
        errors.append(f"WORK_SYSTEM backup contains duplicate shifts.id: {shift_id}")

    request_ids: set[UUID] = set()
    duplicate_request_ids: set[UUID] = set()
    for row in data["shift_requests"]:
        request_id = row.get("id")
        if not request_id:
            continue
        if request_id in request_ids:
            duplicate_request_ids.add(request_id)
        request_ids.add(request_id)
    for request_id in sorted(duplicate_request_ids, key=str):
        errors.append(f"WORK_SYSTEM backup contains duplicate shift_requests.id: {request_id}")

    user_shift_ids: set[UUID] = set()
    duplicate_user_shift_ids: set[UUID] = set()
    for index, row in enumerate(data["user_shifts"]):
        user_shift_id = row.get("id")
        if user_shift_id:
            if user_shift_id in user_shift_ids:
                duplicate_user_shift_ids.add(user_shift_id)
            user_shift_ids.add(user_shift_id)
        if row.get("user_id") not in backup_user_ids:
            errors.append(f"user_shifts[{index}].user_id references missing WORK_SYSTEM users")
        if row.get("shift_id") not in shift_ids:
            errors.append(f"user_shifts[{index}].shift_id references missing shifts")
    for user_shift_id in sorted(duplicate_user_shift_ids, key=str):
        errors.append(f"WORK_SYSTEM backup contains duplicate user_shifts.id: {user_shift_id}")
    for index, row in enumerate(data["shift_requests"]):
        if row.get("user_id") not in backup_user_ids:
            errors.append(f"shift_requests[{index}].user_id references missing WORK_SYSTEM users")
        if row.get("operator_id") and row.get("operator_id") not in backup_operator_ids:
            warnings.append(f"shift_requests[{index}].operator_id is outside WORK_SYSTEM operators and will be restored as NULL")
        if row.get("target_shift_id") not in shift_ids:
            errors.append(f"shift_requests[{index}].target_shift_id references missing shifts")

    audit_log_ids: set[UUID] = set()
    duplicate_audit_log_ids: set[UUID] = set()
    for index, row in enumerate(data["audit_logs"]):
        action_type = row.get("action_type")
        log_id = row.get("id")
        if log_id:
            if log_id in audit_log_ids:
                duplicate_audit_log_ids.add(log_id)
            audit_log_ids.add(log_id)
        if action_type not in WORK_SYSTEM_AUDIT_ACTIONS:
            errors.append(f"audit_logs[{index}].action_type is not allowed for WORK_SYSTEM")
        if row.get("actor_user_id") and row.get("actor_user_id") not in backup_user_ids:
            warnings.append(f"audit_logs[{index}].actor_user_id is outside WORK_SYSTEM users and will be restored as NULL")
        if row.get("target_user_id") and row.get("target_user_id") not in backup_user_ids:
            warnings.append(f"audit_logs[{index}].target_user_id is outside WORK_SYSTEM users and will be restored as NULL")
        if row.get("request_id") and row.get("request_id") not in request_ids:
            warnings.append(f"audit_logs[{index}].request_id is outside WORK_SYSTEM shift_requests and will be restored as NULL")
    for log_id in sorted(duplicate_audit_log_ids, key=str):
        errors.append(f"WORK_SYSTEM backup contains duplicate audit_logs.id: {log_id}")


def _validate_full_foreign_keys(
    db: Session,
    data: dict[str, list[dict]],
    errors: list[str],
    warnings: list[str],
) -> None:
    user_ids = {row.get("id") for row in data["users"] if row.get("id")}
    current_user_ids = {row[0] for row in db.query(models.User.id).all()}
    valid_audit_user_ids = user_ids | current_user_ids
    shift_ids = {row.get("id") for row in data["shifts"] if row.get("id")}
    request_ids = {row.get("id") for row in data["shift_requests"] if row.get("id")}
    notice_ids = {row.get("id") for row in data["notices"] if row.get("id")}
    _validate_internal_foreign_keys("VISITORS", data, errors)
    _validate_internal_foreign_keys("SERIALS", data, errors)
    _validate_user_foreign_keys(None, data, errors, allowed_user_ids=user_ids)
    for index, row in enumerate(data["auth_accounts"]):
        if row.get("user_id") not in user_ids:
            errors.append(f"auth_accounts[{index}].user_id references missing users")
    for index, row in enumerate(data["user_shifts"]):
        if row.get("user_id") not in user_ids:
            errors.append(f"user_shifts[{index}].user_id references missing users")
        if row.get("shift_id") not in shift_ids:
            errors.append(f"user_shifts[{index}].shift_id references missing shifts")
    for index, row in enumerate(data["shift_requests"]):
        if row.get("user_id") not in user_ids:
            errors.append(f"shift_requests[{index}].user_id references missing users")
        if row.get("operator_id") and row.get("operator_id") not in user_ids:
            errors.append(f"shift_requests[{index}].operator_id references missing users")
        if row.get("target_shift_id") not in shift_ids:
            errors.append(f"shift_requests[{index}].target_shift_id references missing shifts")
    for index, row in enumerate(data["notice_targets"]):
        if row.get("notice_id") not in notice_ids:
            errors.append(f"notice_targets[{index}].notice_id references missing notices")
        if row.get("user_id") not in user_ids:
            errors.append(f"notice_targets[{index}].user_id references missing users")
    for index, row in enumerate(data["notice_reads"]):
        if row.get("notice_id") not in notice_ids:
            errors.append(f"notice_reads[{index}].notice_id references missing notices")
        if row.get("user_id") not in user_ids:
            errors.append(f"notice_reads[{index}].user_id references missing users")
    for index, row in enumerate(data.get("audit_logs", [])):
        action_type = row.get("action_type")
        if not isinstance(action_type, str) or not action_type:
            errors.append(f"audit_logs[{index}].action_type must be a string")
        if row.get("actor_user_id") and row.get("actor_user_id") not in valid_audit_user_ids:
            warnings.append(f"audit_logs[{index}].actor_user_id references missing users and will be restored as NULL")
        if row.get("target_user_id") and row.get("target_user_id") not in valid_audit_user_ids:
            warnings.append(f"audit_logs[{index}].target_user_id references missing users and will be restored as NULL")
        if row.get("request_id") and row.get("request_id") not in request_ids:
            warnings.append(f"audit_logs[{index}].request_id references missing shift_requests and will be restored as NULL")
    audit_log_ids: set[UUID] = set()
    duplicate_audit_log_ids: set[UUID] = set()
    for row in data.get("audit_logs", []):
        log_id = row.get("id")
        if not log_id:
            continue
        if log_id in audit_log_ids:
            duplicate_audit_log_ids.add(log_id)
        audit_log_ids.add(log_id)
    for log_id in sorted(duplicate_audit_log_ids, key=str):
        errors.append(f"FULL backup contains duplicate audit_logs.id: {log_id}")


def validate_backup_payload(
    db: Session,
    payload: dict,
    *,
    expected_domain: str | None = None,
    backup_type: str = "JSON",
    allowed_domains: set[str] | None = None,
    allow_sensitive_tables: bool = False,
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
    if domain == "WORK":
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            errors.append("Unsupported schema_version")
    elif schema_version != SCHEMA_VERSION:
        errors.append("Unsupported schema_version")
    allowed_domains = allowed_domains or UPLOAD_RESTORE_DOMAINS
    if domain not in allowed_domains:
        errors.append("Unsupported backup_type/domain")
    if meta_domain and meta_domain not in allowed_domains:
        errors.append("Unsupported meta.domain")
    if backup_type_domain and meta_domain and backup_type_domain != meta_domain:
        errors.append("meta.backup_type and meta.domain do not match")
    if expected_domain and normalize_domain(expected_domain) != domain:
        errors.append("Backup DB domain does not match backup file meta")
    if backup_type != "JSON":
        errors.append("Only JSON backups can be restored")
    if isinstance(data, dict) and not allow_sensitive_tables and ({"users", "auth_accounts"} & set(data.keys())):
        errors.append("users/auth_accounts are not allowed in upload restore")

    if isinstance(data, dict) and domain in allowed_domains:
        required = set(_required_tables(domain))
        actual = set(data.keys())
        legacy_work_audit_logs = domain == "WORK" and "audit_logs" in actual
        if legacy_work_audit_logs:
            warnings.append("WORK backup contains audit_logs; audit_logs will be ignored")
            actual_for_validation = actual - {"audit_logs"}
        else:
            actual_for_validation = actual
        optional_missing = set()
        if domain == "WORK" and "auth_accounts" not in actual_for_validation:
            optional_missing.add("auth_accounts")
        if domain == "FULL" and "audit_logs" not in actual_for_validation:
            optional_missing.add("audit_logs")
            warnings.append("FULL backup does not contain audit_logs; audit_logs merge will be skipped")
        missing = required - actual_for_validation - optional_missing
        unknown = actual_for_validation - required
        if missing:
            errors.append(f"Missing table keys: {', '.join(sorted(missing))}")
        if unknown:
            errors.append(f"Unknown table keys: {', '.join(sorted(unknown))}")
        if domain == "WORK" and not missing:
            work_data = {key: value for key, value in data.items() if key in required}
            if "auth_accounts" not in work_data:
                work_data["auth_accounts"] = []
            coerced = _coerce_data(work_data, domain, errors)
        elif domain == "FULL" and not missing:
            full_data = {key: value for key, value in data.items() if key in required}
            if "audit_logs" not in full_data:
                full_data["audit_logs"] = []
            coerced = _coerce_data(full_data, domain, errors)
        else:
            coerced = _coerce_data(data, domain, errors) if not missing else {name: [] for name in required}
        if not missing and not unknown:
            if domain in SUPPORTED_DOMAINS:
                _validate_internal_foreign_keys(domain, coerced, errors)
                _validate_user_foreign_keys(db, coerced, errors)
            elif domain == "WORK":
                _validate_work_foreign_keys(db, coerced, errors, warnings)
            elif domain == "WORK_SYSTEM":
                _validate_work_system_foreign_keys(db, coerced, errors, warnings)
            elif domain == "FULL":
                _validate_full_foreign_keys(db, coerced, errors, warnings)
    else:
        coerced = {}

    if isinstance(meta, dict) and isinstance(data, dict) and isinstance(checksum, dict):
        _validate_checksum(payload, errors)

    summary = {table_name: len(rows) for table_name, rows in data.items()} if isinstance(data, dict) else {}
    return {
        "valid": not errors,
        "domain": domain if domain in allowed_domains else None,
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
        allowed_domains=SERVER_RESTORE_DOMAINS,
        allow_sensitive_tables=True,
    )
    result["errors"] = errors + result.get("errors", [])
    result["valid"] = not result["errors"]
    return result


def _insert_rows(db: Session, domain: str, data: dict[str, list[dict]]) -> None:
    for table_name in _insert_order(domain):
        model = TABLE_MODEL_MAP[table_name]
        for row in data[table_name]:
            db.add(model(**row))
        db.flush()


def _delete_domain_rows(db: Session, domain: str) -> None:
    if domain == "FULL":
        db.query(models.AuditLog).update(
            {
                models.AuditLog.actor_user_id: None,
                models.AuditLog.target_user_id: None,
                models.AuditLog.request_id: None,
            },
            synchronize_session=False,
        )
        db.query(models.DataBackup).update(
            {models.DataBackup.created_by: None},
            synchronize_session=False,
        )
        db.query(models.DataRestoreJob).update(
            {models.DataRestoreJob.requested_by: None},
            synchronize_session=False,
        )
    for table_name in _delete_order(domain):
        db.query(TABLE_MODEL_MAP[table_name]).delete(synchronize_session=False)
    db.flush()


def _work_member_rows(data: dict[str, list[dict]]) -> list[dict]:
    return [
        row for row in data["users"]
        if _role_value(row.get("role")) == models.UserRole.MEMBER.value and row.get("id")
    ]


def _upsert_work_members(db: Session, data: dict[str, list[dict]]) -> set[UUID]:
    member_rows = _work_member_rows(data)
    backup_member_ids = {row["id"] for row in member_rows}
    for row in member_rows:
        existing = db.query(models.User).filter(models.User.id == row["id"]).first()
        if existing:
            if existing.role != models.UserRole.MEMBER:
                raise ValueError("WORK restore cannot modify MASTER/OPERATOR users")
            if row.get("name") is not None:
                existing.name = row.get("name")
            if "identifier" in row:
                existing.identifier = row.get("identifier")
            existing.role = models.UserRole.MEMBER
            if row.get("active") is not None:
                existing.active = row.get("active")
        else:
            db.add(
                models.User(
                    id=row["id"],
                    name=row.get("name") or "Restored Member",
                    identifier=row.get("identifier"),
                    role=models.UserRole.MEMBER,
                    active=True if row.get("active") is None else row.get("active"),
                    created_at=row.get("created_at") or datetime.now(timezone.utc),
                    updated_at=row.get("updated_at") or datetime.now(timezone.utc),
                )
            )
    query = db.query(models.User).filter(models.User.role == models.UserRole.MEMBER)
    if backup_member_ids:
        query = query.filter(~models.User.id.in_(backup_member_ids))
    for user in query.all():
        user.active = False
    db.flush()
    return backup_member_ids


def _upsert_work_auth_accounts(db: Session, data: dict[str, list[dict]], backup_member_ids: set[UUID]) -> None:
    for row in data.get("auth_accounts", []):
        user_id = row.get("user_id")
        if user_id not in backup_member_ids:
            continue
        login_id = row.get("login_id")
        password_hash = row.get("password_hash")
        if not login_id or not password_hash:
            raise ValueError("WORK auth_accounts require login_id and password_hash")
        conflict = db.query(models.AuthAccount).filter(
            models.AuthAccount.login_id == login_id,
            models.AuthAccount.user_id != user_id,
        ).first()
        if conflict:
            raise ValueError("WORK auth_accounts login_id conflict")
        account = db.query(models.AuthAccount).filter(models.AuthAccount.user_id == user_id).first()
        if account:
            account.login_id = login_id
            account.password_hash = password_hash
            account.last_login_at = row.get("last_login_at")
        else:
            db.add(
                models.AuthAccount(
                    user_id=user_id,
                    login_id=login_id,
                    password_hash=password_hash,
                    last_login_at=row.get("last_login_at"),
                )
            )
    db.flush()


def _restore_work_rows(db: Session, data: dict[str, list[dict]]) -> None:
    pre_restore_user_ids = {row[0] for row in db.query(models.User.id).all()}
    backup_member_ids = _upsert_work_members(db, data)
    _upsert_work_auth_accounts(db, data, backup_member_ids)

    deleting_request_ids = [row[0] for row in db.query(models.ShiftRequest.id).all()]
    if deleting_request_ids:
        db.query(models.AuditLog).filter(models.AuditLog.request_id.in_(deleting_request_ids)).update(
            {models.AuditLog.request_id: None},
            synchronize_session=False,
        )
    db.query(models.ShiftRequest).delete(synchronize_session=False)
    db.query(models.UserShift).delete(synchronize_session=False)
    db.query(models.Shift).delete(synchronize_session=False)
    db.flush()

    for row in data["shifts"]:
        db.add(models.Shift(**row))
    db.flush()
    for row in data["user_shifts"]:
        db.add(models.UserShift(**row))
    db.flush()
    for row in data["shift_requests"]:
        restored = dict(row)
        if restored.get("operator_id") and restored["operator_id"] not in pre_restore_user_ids:
            restored["operator_id"] = None
        db.add(models.ShiftRequest(**restored))


def _work_system_user_rows(data: dict[str, list[dict]]) -> list[dict]:
    return [
        row for row in data["users"]
        if _role_value(row.get("role")) in {models.UserRole.OPERATOR.value, models.UserRole.MEMBER.value}
        and row.get("id")
    ]


def _upsert_work_system_users(db: Session, data: dict[str, list[dict]]) -> set[UUID]:
    user_rows = _work_system_user_rows(data)
    backup_user_ids = {row["id"] for row in user_rows}
    for row in user_rows:
        role = models.UserRole(_role_value(row.get("role")))
        existing = db.query(models.User).filter(models.User.id == row["id"]).first()
        if existing:
            if existing.role == models.UserRole.MASTER:
                raise ValueError("WORK_SYSTEM restore cannot modify MASTER users")
            if row.get("name") is not None:
                existing.name = row.get("name")
            if "identifier" in row:
                existing.identifier = row.get("identifier")
            existing.role = role
            if row.get("active") is not None:
                existing.active = row.get("active")
            if row.get("updated_at") is not None:
                existing.updated_at = row.get("updated_at")
        else:
            db.add(
                models.User(
                    id=row["id"],
                    name=row.get("name") or "Restored User",
                    identifier=row.get("identifier"),
                    role=role,
                    active=True if row.get("active") is None else row.get("active"),
                    created_at=row.get("created_at") or datetime.now(timezone.utc),
                    updated_at=row.get("updated_at") or datetime.now(timezone.utc),
                )
            )
    query = db.query(models.User).filter(
        models.User.role.in_((models.UserRole.OPERATOR, models.UserRole.MEMBER))
    )
    if backup_user_ids:
        query = query.filter(~models.User.id.in_(backup_user_ids))
    for user in query.all():
        user.active = False
    db.flush()
    return backup_user_ids


def _upsert_work_system_auth_accounts(db: Session, data: dict[str, list[dict]], backup_user_ids: set[UUID]) -> None:
    for row in data["auth_accounts"]:
        user_id = row.get("user_id")
        if user_id not in backup_user_ids:
            continue
        login_id = row.get("login_id")
        password_hash = row.get("password_hash")
        if not login_id or not password_hash:
            raise ValueError("WORK_SYSTEM auth_accounts require login_id and password_hash")
        conflict = db.query(models.AuthAccount).filter(
            models.AuthAccount.login_id == login_id,
            models.AuthAccount.user_id != user_id,
        ).first()
        if conflict:
            raise ValueError("WORK_SYSTEM auth_accounts login_id conflict")
        account = db.query(models.AuthAccount).filter(models.AuthAccount.user_id == user_id).first()
        if account:
            account.login_id = login_id
            account.password_hash = password_hash
            account.last_login_at = row.get("last_login_at")
        else:
            db.add(
                models.AuthAccount(
                    user_id=user_id,
                    login_id=login_id,
                    password_hash=password_hash,
                    last_login_at=row.get("last_login_at"),
                )
            )
    db.flush()


def _restore_work_system_rows(db: Session, data: dict[str, list[dict]]) -> None:
    backup_user_ids = _upsert_work_system_users(db, data)
    _upsert_work_system_auth_accounts(db, data, backup_user_ids)

    deleting_request_ids = [row[0] for row in db.query(models.ShiftRequest.id).all()]
    if deleting_request_ids:
        db.query(models.AuditLog).filter(models.AuditLog.request_id.in_(deleting_request_ids)).update(
            {models.AuditLog.request_id: None},
            synchronize_session=False,
        )
    db.query(models.ShiftRequest).delete(synchronize_session=False)
    db.query(models.UserShift).delete(synchronize_session=False)
    db.query(models.Shift).delete(synchronize_session=False)
    db.flush()

    for row in data["shifts"]:
        db.add(models.Shift(**row))
    db.flush()
    for row in data["user_shifts"]:
        db.add(models.UserShift(**row))
    db.flush()

    operator_ids = {
        row["id"] for row in _work_system_user_rows(data)
        if _role_value(row.get("role")) == models.UserRole.OPERATOR.value
    }
    for row in data["shift_requests"]:
        restored = dict(row)
        if restored.get("operator_id") and restored["operator_id"] not in operator_ids:
            restored["operator_id"] = None
        db.add(models.ShiftRequest(**restored))
    db.flush()

    valid_user_ids = set(backup_user_ids)
    restored_request_ids = {row[0] for row in db.query(models.ShiftRequest.id).all()}
    existing_log_ids = {row[0] for row in db.query(models.AuditLog.id).all()}
    for row in data["audit_logs"]:
        log_id = row.get("id")
        if log_id and log_id in existing_log_ids:
            continue
        restored = dict(row)
        if restored.get("actor_user_id") not in valid_user_ids:
            restored["actor_user_id"] = None
        if restored.get("target_user_id") not in valid_user_ids:
            restored["target_user_id"] = None
        if restored.get("request_id") not in restored_request_ids:
            restored["request_id"] = None
        db.add(models.AuditLog(**restored))


def _merge_full_audit_logs(db: Session, data: dict[str, list[dict]]) -> int:
    valid_user_ids = {row[0] for row in db.query(models.User.id).all()}
    valid_request_ids = {row[0] for row in db.query(models.ShiftRequest.id).all()}
    existing_log_ids = {row[0] for row in db.query(models.AuditLog.id).all()}
    inserted_count = 0
    for row in data.get("audit_logs", []):
        log_id = row.get("id")
        if log_id and log_id in existing_log_ids:
            continue
        restored = dict(row)
        if restored.get("id") is None:
            restored.pop("id", None)
        if restored.get("actor_user_id") not in valid_user_ids:
            restored["actor_user_id"] = None
        if restored.get("target_user_id") not in valid_user_ids:
            restored["target_user_id"] = None
        if restored.get("request_id") not in valid_request_ids:
            restored["request_id"] = None
        db.add(models.AuditLog(**restored))
        if log_id:
            existing_log_ids.add(log_id)
        inserted_count += 1
    return inserted_count


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
        description=f"Restore point before restore job {job.id}",
        file_name=f"restore_point_{stamp}_{domain.lower()}.json",
        kind="RESTORE_POINT",
    )
    if domain == "WORK":
        _restore_work_rows(db, validation["_coerced_data"])
    elif domain == "WORK_SYSTEM":
        _restore_work_system_rows(db, validation["_coerced_data"])
    else:
        _delete_domain_rows(db, domain)
        _insert_rows(db, domain, validation["_coerced_data"])
    audit_logs_merged = 0
    if domain == "FULL":
        audit_logs_merged = _merge_full_audit_logs(db, validation["_coerced_data"])
    job.status = "SUCCESS"
    job.finished_at = datetime.now(timezone.utc)
    summary = {
        "restored": validation["summary"],
        "pre_restore_backup_id": str(pre_restore.id),
        "restore_point_backup_id": str(pre_restore.id),
    }
    if domain == "FULL":
        summary["audit_logs_merged"] = audit_logs_merged
    job.summary = summary
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
        description=f"Restore point before upload restore job {job.id}",
        file_name=f"restore_point_{stamp}_{domain.lower()}.json",
        kind="RESTORE_POINT",
    )
    _delete_domain_rows(db, domain)
    _insert_rows(db, domain, validation["_coerced_data"])
    job.status = "SUCCESS"
    job.finished_at = datetime.now(timezone.utc)
    job.summary = {
        "restored": validation["summary"],
        "pre_restore_backup_id": str(pre_restore.id),
        "restore_point_backup_id": str(pre_restore.id),
        "source": "UPLOAD",
    }
    return job, validation
