from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from .. import models
from ..config import get_settings
from ..core.backup_manifest import BACKUP_DOMAINS, SCHEMA_VERSION
from .backup_service import normalize_domain, validate_work_system_backup_payload
from .restore_service import validate_backup_payload


DOMAIN_STORAGE_DIRS = {
    "FULL": (Path("system") / "full",),
    "WORK": (Path("work") / "manual", Path("work") / "restore_points"),
    "WORK_SYSTEM": (Path("work_system") / "manual", Path("work_system") / "restore_points"),
    "VISITORS": (Path("visitors") / "manual", Path("visitors") / "restore_points"),
    "SERIALS": (Path("serials") / "manual", Path("serials") / "restore_points"),
}

FORBIDDEN_SENSITIVE_KEYWORDS = (
    "password",
    "password_hash",
    "token",
    "secret",
    "key",
    "credential",
    "session",
    "refresh",
    "access_token",
)

DEFAULT_MASKED_FIELDS = {"name", "identifier", "login_id", "email", "phone"}
WORK_USER_PREVIEW_COLUMNS = {"id", "name", "identifier", "role", "active"}
SAMPLE_LIMIT = 3


def _storage_root() -> Path:
    return Path(get_settings().BACKUP_STORAGE_DIR).resolve()


def _is_inside_storage(path: Path) -> bool:
    root = _storage_root()
    resolved = path.resolve()
    return resolved == root or root in resolved.parents


def _relative_path(path: Path) -> str:
    return path.resolve().relative_to(_storage_root()).as_posix()


def encode_storage_key(path: Path) -> str:
    relative = _relative_path(path)
    return base64.urlsafe_b64encode(relative.encode("utf-8")).decode("ascii").rstrip("=")


def resolve_storage_key(storage_key: str) -> Path:
    if not storage_key:
        raise ValueError("storage_key is required")
    try:
        padded = storage_key + "=" * (-len(storage_key) % 4)
        relative = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Invalid storage_key") from exc
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("Invalid storage_key path")
    file_path = (_storage_root() / relative_path).resolve()
    if not _is_inside_storage(file_path):
        raise PermissionError("Backup file is outside storage directory")
    if file_path.suffix.lower() != ".json":
        raise ValueError("Only .json backup files are allowed")
    if not file_path.is_file():
        raise FileNotFoundError("Backup file not found")
    return file_path


def _safe_registered_backup_path(backup: models.DataBackup) -> Path:
    file_path = Path(backup.file_path).resolve()
    if not _is_inside_storage(file_path):
        raise PermissionError("Backup file is outside storage directory")
    if file_path.suffix.lower() != ".json":
        raise ValueError("Only .json backup files are allowed")
    if not file_path.is_file():
        raise FileNotFoundError("Backup file not found")
    return file_path


def _domain_from_payload(payload: dict) -> str:
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    if not isinstance(meta, dict):
        return ""
    return normalize_domain(meta.get("backup_type") or meta.get("domain") or "")


def _kind_from_payload_or_path(payload: dict | None, file_path: Path) -> str:
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    if isinstance(meta, dict):
        kind = normalize_domain(meta.get("kind") or meta.get("backup_kind") or "")
        if kind:
            return kind
    relative_parts = set(_relative_path(file_path).split("/"))
    if "restore_points" in relative_parts:
        return "RESTORE_POINT"
    return "MANUAL"


def _parse_created_at(payload: dict) -> tuple[datetime, str | None]:
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    raw = meta.get("created_at") if isinstance(meta, dict) else None
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed, str(raw)
        except ValueError:
            pass
    return datetime.now(timezone.utc), str(raw) if raw else None


def _registered_by_path(db: Session) -> dict[str, models.DataBackup]:
    rows = db.query(models.DataBackup).filter(models.DataBackup.deleted_at.is_(None)).all()
    registered: dict[str, models.DataBackup] = {}
    for row in rows:
        try:
            resolved = str(Path(row.file_path).resolve())
        except (OSError, RuntimeError):
            continue
        registered[resolved] = row
    return registered


def _find_registered_by_resolved_path(db: Session, resolved_path: str) -> models.DataBackup | None:
    for backup in db.query(models.DataBackup).filter(models.DataBackup.deleted_at.is_(None)).all():
        try:
            if str(Path(backup.file_path).resolve()) == resolved_path:
                return backup
        except (OSError, RuntimeError):
            continue
    return None


def _candidate_files(domain: str) -> list[Path]:
    root = _storage_root()
    candidates: dict[str, Path] = {}
    for subdir in DOMAIN_STORAGE_DIRS.get(domain, ()):
        folder = root / subdir
        if folder.is_dir():
            for path in folder.rglob("*.json"):
                if _is_inside_storage(path):
                    candidates[str(path.resolve())] = path.resolve()
    if root.is_dir():
        for path in root.glob("*.json"):
            if path.is_file() and _is_inside_storage(path):
                candidates[str(path.resolve())] = path.resolve()
    return sorted(candidates.values(), key=lambda path: path.stat().st_mtime, reverse=True)


def _is_domain_storage_file(file_path: Path, domain: str) -> bool:
    resolved = file_path.resolve()
    for subdir in DOMAIN_STORAGE_DIRS.get(domain, ()):
        folder = (_storage_root() / subdir).resolve()
        if resolved == folder or folder in resolved.parents:
            return True
    return False


def _load_payload(file_path: Path) -> tuple[dict | None, list[str]]:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"Invalid JSON: {exc.msg}"]
    except OSError as exc:
        return None, [f"Unable to read backup file: {exc}"]
    if not isinstance(payload, dict):
        return None, ["Backup root must be an object"]
    return payload, []


def _is_forbidden_sensitive_field(field_name: str) -> bool:
    lowered = field_name.lower()
    return any(keyword in lowered for keyword in FORBIDDEN_SENSITIVE_KEYWORDS)


def _mask_string(value: str) -> str:
    if not value:
        return value
    if len(value) <= 1:
        return "*"
    return f"{value[0]}{'*' * max(1, len(value) - 1)}"


def _mask_value(field_name: str, value, *, sensitive: bool):
    if value is None:
        return None
    if _is_forbidden_sensitive_field(field_name):
        return "[MASKED]"
    if isinstance(value, dict):
        return {
            key: _mask_value(str(key), nested_value, sensitive=sensitive)
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_mask_value(field_name, item, sensitive=sensitive) for item in value]
    if not sensitive and field_name.lower() in DEFAULT_MASKED_FIELDS:
        if isinstance(value, str):
            return _mask_string(value)
        return "[MASKED]"
    return value


def _sample_row(table_name: str, row: dict, *, domain: str, sensitive: bool) -> dict:
    if domain in {"WORK", "WORK_SYSTEM"} and table_name == "users":
        row = {key: row.get(key) for key in WORK_USER_PREVIEW_COLUMNS if key in row}
    sampled = {}
    for key, value in row.items():
        sampled[key] = _mask_value(key, value, sensitive=sensitive)
    return sampled


def _table_samples(table_name: str, rows: list, *, domain: str, sensitive: bool) -> list[dict]:
    if domain == "FULL" and table_name == "auth_accounts" and not sensitive:
        return []
    samples = []
    for row in rows[:SAMPLE_LIMIT]:
        if isinstance(row, dict):
            samples.append(_sample_row(table_name, row, domain=domain, sensitive=sensitive))
    return samples


def build_backup_preview(
    db: Session,
    *,
    backup: models.DataBackup,
    sensitive: bool = False,
) -> dict:
    domain = normalize_domain(backup.domain)
    file_path = _safe_registered_backup_path(backup)
    payload, load_errors = _load_payload(file_path)
    if payload is None:
        raise ValueError(load_errors[0] if load_errors else "Invalid backup file")

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    checksum = payload.get("checksum") if isinstance(payload.get("checksum"), dict) else {}
    if domain == "WORK_SYSTEM":
        validation = validate_work_system_backup_payload(payload, expected_domain=domain)
    else:
        validation = validate_backup_payload(
            db,
            payload,
            expected_domain=domain,
            backup_type=backup.backup_type,
            allowed_domains=BACKUP_DOMAINS,
            allow_sensitive_tables=True,
        )
    errors = load_errors + validation.get("errors", [])
    warnings = list(validation.get("warnings", []))
    if sensitive:
        warnings.append("민감정보 포함 미리보기 요청이 기록되었습니다. 비밀번호, 토큰, secret 계열 필드는 항상 마스킹됩니다.")
    else:
        warnings.append("민감정보는 기본적으로 마스킹되었습니다.")
    if domain == "FULL" and "auth_accounts" in data and not sensitive:
        warnings.append("auth_accounts 샘플은 기본 미리보기에서 제외되었습니다.")

    summary = {
        table_name: len(rows) if isinstance(rows, list) else 0
        for table_name, rows in data.items()
    }
    samples = {
        table_name: _table_samples(table_name, rows, domain=domain, sensitive=sensitive)
        for table_name, rows in data.items()
        if isinstance(rows, list)
    }
    return {
        "backup_id": backup.id,
        "domain": domain,
        "kind": backup.kind,
        "schema_version": meta.get("schema_version") or backup.schema_version,
        "created_at": meta.get("created_at") or backup.created_at,
        "file_size": backup.file_size,
        "checksum": checksum.get("value") or backup.checksum,
        "summary": summary,
        "samples": samples,
        "warnings": warnings,
        "errors": errors,
        "sensitive": sensitive,
        "masked": True,
    }


def _item_for_file(db: Session, file_path: Path, domain: str, registered: dict[str, models.DataBackup]) -> dict | None:
    payload, errors = _load_payload(file_path)
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    file_domain = _domain_from_payload(payload or {})
    if payload is not None and file_domain and file_domain != domain:
        if not _is_domain_storage_file(file_path, domain):
            return None
        stat = file_path.stat()
        return {
            "storage_key": encode_storage_key(file_path),
            "file_name": file_path.name,
            "display_path": _relative_path(file_path),
            "file_size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            "registered": registered.get(str(file_path.resolve())) is not None,
            "backup_id": registered.get(str(file_path.resolve())).id if registered.get(str(file_path.resolve())) else None,
            "domain": file_domain,
            "kind": _kind_from_payload_or_path(payload, file_path),
            "schema_version": meta.get("schema_version") if isinstance(meta, dict) else None,
            "checksum_match": None,
            "status": "DOMAIN_MISMATCH",
            "errors": [f"Backup file domain {file_domain} does not match requested domain {domain}"],
        }
    if payload is not None and not file_domain:
        errors.append("Backup domain is missing")

    validation = None
    if payload is not None and file_domain == domain:
        if domain == "WORK_SYSTEM":
            validation = validate_work_system_backup_payload(payload, expected_domain=domain)
        else:
            validation = validate_backup_payload(
                db,
                payload,
                expected_domain=domain,
                backup_type="JSON",
                allowed_domains=BACKUP_DOMAINS,
                allow_sensitive_tables=True,
            )
        errors.extend(validation.get("errors", []))

    registered_backup = registered.get(str(file_path.resolve()))
    checksum_match = None
    status = "INVALID"
    if payload is not None and validation:
        checksum_match = "checksum mismatch" not in validation.get("errors", [])
        if not validation.get("valid"):
            status = "CHECKSUM_MISMATCH" if not checksum_match else "INVALID"
        elif registered_backup:
            status = "REGISTERED"
        else:
            status = "UNREGISTERED"

    stat = file_path.stat()
    return {
        "storage_key": encode_storage_key(file_path),
        "file_name": file_path.name,
        "display_path": _relative_path(file_path),
        "file_size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        "registered": registered_backup is not None,
        "backup_id": registered_backup.id if registered_backup else None,
        "domain": file_domain or domain,
        "kind": _kind_from_payload_or_path(payload, file_path),
        "schema_version": meta.get("schema_version") if isinstance(meta, dict) else None,
        "checksum_match": checksum_match,
        "status": status,
        "errors": errors,
    }


def list_storage_backup_files(db: Session, domain: str) -> list[dict]:
    domain = normalize_domain(domain)
    if domain not in BACKUP_DOMAINS:
        raise ValueError("unsupported_domain")
    registered = _registered_by_path(db)
    items = []
    for file_path in _candidate_files(domain):
        item = _item_for_file(db, file_path, domain, registered)
        if item:
            items.append(item)
    return items


def validate_storage_backup_file(db: Session, *, domain: str, storage_key: str) -> dict:
    domain = normalize_domain(domain)
    if domain not in BACKUP_DOMAINS:
        raise ValueError("unsupported_domain")
    file_path = resolve_storage_key(storage_key)
    payload, load_errors = _load_payload(file_path)
    if payload is None:
        return {
            "valid": False,
            "domain": None,
            "schema_version": None,
            "summary": {},
            "warnings": [],
            "errors": load_errors,
        }
    if domain == "WORK_SYSTEM":
        result = validate_work_system_backup_payload(payload, expected_domain=domain)
    else:
        result = validate_backup_payload(
            db,
            payload,
            expected_domain=domain,
            backup_type="JSON",
            allowed_domains=BACKUP_DOMAINS,
            allow_sensitive_tables=True,
        )
    result["errors"] = load_errors + result.get("errors", [])
    result["valid"] = not result["errors"]
    return result


def register_storage_backup_file(
    db: Session,
    *,
    domain: str,
    storage_key: str,
    description: str | None,
    current_user: models.User,
) -> tuple[models.DataBackup, dict]:
    domain = normalize_domain(domain)
    file_path = resolve_storage_key(storage_key)
    resolved_path = str(file_path.resolve())
    existing = _find_registered_by_resolved_path(db, resolved_path)
    if existing:
        raise FileExistsError("Backup file is already registered")

    validation = validate_storage_backup_file(db, domain=domain, storage_key=storage_key)
    if not validation.get("valid"):
        raise ValueError("Backup file validation failed")

    payload, _ = _load_payload(file_path)
    created_at, original_created_at = _parse_created_at(payload or {})
    backup = models.DataBackup(
        domain=domain,
        backup_type="JSON",
        kind=_kind_from_payload_or_path(payload, file_path),
        file_name=file_path.name,
        file_path=resolved_path,
        file_size=file_path.stat().st_size,
        checksum=(payload or {}).get("checksum", {}).get("value"),
        schema_version=validation.get("schema_version") or SCHEMA_VERSION,
        status="READY",
        description=description or "Storage file re-registered",
        created_by=current_user.id,
        created_at=created_at,
    )
    db.add(backup)
    db.flush()
    return backup, {
        "display_path": _relative_path(file_path),
        "original_created_at": original_created_at,
    }
