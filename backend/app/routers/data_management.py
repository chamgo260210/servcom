from __future__ import annotations

import json
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from urllib.parse import quote

from .. import models, schemas
from ..core.audit import record_log
from ..core.backup_manifest import BACKUP_DOMAINS, PHASE1_REJECTED_DOMAINS
from ..config import get_settings
from ..deps import get_db
from ..core.roles import require_role
from ..services.backup_service import (
    build_sanitized_backup_payload,
    create_json_backup,
    normalize_domain,
)
from ..services.backup_storage_service import (
    build_backup_preview,
    list_storage_backup_files,
    register_storage_backup_file,
    validate_storage_backup_file,
)
from ..services.excel_export_service import build_serials_excel, build_visitors_excel
from ..services.restore_service import (
    _domain_changed_after_restore,
    parse_backup_json_bytes,
    restore_backup,
    restore_uploaded_backup,
    validate_backup_file,
    validate_backup_payload,
)

router = APIRouter(prefix="/data", tags=["data-management"])

RESET_CUTOFF_SCOPES_BY_DOMAIN = {
    "WORK": ("members", "all"),
    "WORK_SYSTEM": ("operators_members", "all"),
    "VISITORS": ("visitors_all", "all"),
    "SERIALS": ("serials_all", "all"),
    "FULL": ("all",),
}


def _audit_details(log: models.AuditLog) -> dict:
    return log.details if isinstance(log.details, dict) else {}


def _latest_reset_cutoff(db: Session, domain: str) -> tuple[datetime | None, str | None, tuple[str, ...]]:
    requested_domain = normalize_domain(domain)
    reset_scopes = RESET_CUTOFF_SCOPES_BY_DOMAIN.get(requested_domain)
    if not reset_scopes:
        raise HTTPException(status_code=400, detail="Unsupported backup domain")
    logs = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.action_type == "RESET_DATA")
        .order_by(models.AuditLog.created_at.desc())
        .limit(200)
        .all()
    )
    for log in logs:
        scope = _audit_details(log).get("scope")
        if scope in reset_scopes:
            return log.created_at, scope, reset_scopes
    return None, None, reset_scopes


def _reject_unsupported_domain(domain: str) -> str:
    normalized = normalize_domain(domain)
    if normalized in PHASE1_REJECTED_DOMAINS:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"{normalized} backup is not available in Phase 1",
        )
    if normalized not in BACKUP_DOMAINS:
        raise HTTPException(status_code=400, detail="Unsupported backup domain")
    return normalized


def _get_backup_or_404(db: Session, backup_id) -> models.DataBackup:
    backup = (
        db.query(models.DataBackup)
        .filter(models.DataBackup.id == backup_id, models.DataBackup.deleted_at.is_(None))
        .first()
    )
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")
    return backup


def _validation_response(result: dict) -> dict:
    return {
        "valid": result.get("valid", False),
        "domain": result.get("domain"),
        "schema_version": result.get("schema_version"),
        "summary": result.get("summary") or {},
        "warnings": result.get("warnings") or [],
        "errors": result.get("errors") or [],
    }


def _ensure_domain_access(domain: str, current_user, *, action: str = "access") -> str:
    normalized = normalize_domain(domain)
    if normalized not in BACKUP_DOMAINS:
        raise HTTPException(status_code=400, detail="Unsupported backup domain")
    if normalized in {"FULL", "WORK_SYSTEM"} and current_user.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail=f"Only masters can {action} {normalized} backups")
    return normalized


def _actor_snapshot(current_user) -> dict:
    return {
        "id": str(current_user.id),
        "name": current_user.name,
        "identifier": current_user.identifier,
        "role": current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
    }


def _restore_actor_id(domain: str, current_user) -> str | None:
    return None if normalize_domain(domain) == "FULL" else str(current_user.id)


def _restore_details(domain: str, current_user, details: dict) -> dict:
    if normalize_domain(domain) == "FULL":
        return {**details, "actor_snapshot": _actor_snapshot(current_user)}
    return details


def _validate_registered_backup(db: Session, backup: models.DataBackup) -> dict:
    return validate_backup_file(db, backup)


def _restore_point_id_from_summary(summary: dict | None) -> str | None:
    if not isinstance(summary, dict):
        return None
    return summary.get("restore_point_backup_id") or summary.get("pre_restore_backup_id")


def _safe_backup_file_exists(backup: models.DataBackup) -> bool:
    try:
        storage_root = Path(get_settings().BACKUP_STORAGE_DIR).resolve()
        file_path = Path(backup.file_path).resolve()
    except (OSError, RuntimeError):
        return False
    if file_path != storage_root and storage_root not in file_path.parents:
        return False
    return file_path.is_file()


def _get_restore_point_backup(
    db: Session,
    restore_job: models.DataRestoreJob,
    *,
    include_deleted: bool = False,
) -> models.DataBackup | None:
    restore_point_id = _restore_point_id_from_summary(restore_job.summary)
    if not restore_point_id:
        return None
    try:
        backup_uuid = UUID(str(restore_point_id))
    except ValueError:
        return None
    query = db.query(models.DataBackup).filter(models.DataBackup.id == backup_uuid)
    if not include_deleted:
        query = query.filter(models.DataBackup.deleted_at.is_(None))
    return query.first()


def _restore_point_info(db: Session, restore_job: models.DataRestoreJob) -> dict:
    restore_point_id = _restore_point_id_from_summary(restore_job.summary)
    if not restore_point_id:
        return {"id": None, "status": "NONE", "file_exists": False}
    try:
        UUID(str(restore_point_id))
    except ValueError:
        return {"id": str(restore_point_id), "status": "INVALID_ID", "file_exists": False}
    backup = _get_restore_point_backup(db, restore_job, include_deleted=True)
    if not backup:
        return {"id": str(restore_point_id), "status": "MISSING_ROW", "file_exists": False}
    file_exists = _safe_backup_file_exists(backup)
    if backup.deleted_at is not None:
        status = "DELETED"
    elif backup.kind != "RESTORE_POINT":
        status = "INVALID_KIND"
    elif not file_exists:
        status = "MISSING_FILE"
    else:
        status = backup.status or "READY"
    return {
        "id": str(backup.id),
        "file_name": backup.file_name,
        "created_at": backup.created_at.isoformat() if backup.created_at else None,
        "status": status,
        "kind": backup.kind,
        "file_exists": file_exists,
        "deleted_at": backup.deleted_at.isoformat() if backup.deleted_at else None,
    }


ROLLBACK_UNAVAILABLE_MESSAGES = {
    "INVALID_STATUS": "되돌릴 수 없는 복원 상태입니다.",
    "RESTORE_COMPLETION_UNKNOWN": "복원 완료 시각을 확인할 수 없어 되돌릴 수 없습니다.",
    "ROLLBACK_ALREADY_USED": "이미 되돌린 복원입니다.",
    "RESTORE_POINT_MISSING": "복원 전 지점 파일이 없어 되돌릴 수 없습니다.",
    "RECENT_RESTORE_ONLY": "해당 도메인의 최근 복원만 되돌릴 수 있습니다.",
    "DOMAIN_CHANGED_AFTER_RESTORE": "복원 이후 해당 도메인 데이터가 변경되어 되돌릴 수 없습니다.",
}


def _latest_success_restore_job_id(db: Session, domain: str | None = None):
    query = db.query(models.DataRestoreJob).filter(
        models.DataRestoreJob.status == "SUCCESS",
        models.DataRestoreJob.mode == "REPLACE",
    )
    if domain:
        query = query.filter(models.DataRestoreJob.domain == normalize_domain(domain))
    latest_job = (
        query
        .order_by(models.DataRestoreJob.finished_at.desc(), models.DataRestoreJob.started_at.desc())
        .first()
    )
    return latest_job.id if latest_job else None


def _rollback_unavailable_reason(
    db: Session,
    job: models.DataRestoreJob,
    *,
    latest_success_restore_job_id=None,
) -> str | None:
    summary = dict(job.summary or {})
    domain = normalize_domain(job.domain)
    if job.status != "SUCCESS" or job.mode != "REPLACE":
        return "INVALID_STATUS"
    if job.finished_at is None:
        return "RESTORE_COMPLETION_UNKNOWN"
    if summary.get("rollback_used"):
        return "ROLLBACK_ALREADY_USED"
    restore_point_backup = _get_restore_point_backup(db, job)
    if (
        not bool(_restore_point_id_from_summary(summary))
        or restore_point_backup is None
        or restore_point_backup.kind != "RESTORE_POINT"
        or normalize_domain(restore_point_backup.domain) != domain
        or not _safe_backup_file_exists(restore_point_backup)
    ):
        return "RESTORE_POINT_MISSING"
    if latest_success_restore_job_id is None:
        latest_success_restore_job_id = _latest_success_restore_job_id(db, domain)
    if job.id != latest_success_restore_job_id:
        return "RECENT_RESTORE_ONLY"
    if _domain_changed_after_restore(db, domain, job.finished_at, job.id):
        return "DOMAIN_CHANGED_AFTER_RESTORE"
    return None


def _raise_rollback_unavailable(reason: str) -> None:
    raise HTTPException(status_code=400, detail=ROLLBACK_UNAVAILABLE_MESSAGES[reason])


def _latest_success_restore_job_ids_by_domain(db: Session) -> dict[str, object]:
    jobs = (
        db.query(models.DataRestoreJob)
        .filter(
            models.DataRestoreJob.status == "SUCCESS",
            models.DataRestoreJob.mode == "REPLACE",
        )
        .order_by(
            models.DataRestoreJob.domain.asc(),
            models.DataRestoreJob.finished_at.desc(),
            models.DataRestoreJob.started_at.desc(),
        )
        .all()
    )
    latest_by_domain = {}
    for job in jobs:
        domain = normalize_domain(job.domain)
        if domain not in latest_by_domain:
            latest_by_domain[domain] = job.id
    return latest_by_domain


def _restore_job_response(
    db: Session,
    job: models.DataRestoreJob,
    current_user,
    latest_success_restore_job_id=None,
) -> dict:
    summary = dict(job.summary or {})
    domain = normalize_domain(job.domain)
    if latest_success_restore_job_id is None:
        latest_success_restore_job_id = _latest_success_restore_job_id(db, domain)
    is_latest_success_restore = job.id == latest_success_restore_job_id
    try:
        restore_point_backup = _get_restore_point_backup(db, job)
        restore_point_info = _restore_point_info(db, job)
        rollback_unavailable_reason = _rollback_unavailable_reason(
            db,
            job,
            latest_success_restore_job_id=latest_success_restore_job_id,
        )
        rollback_available = (
            rollback_unavailable_reason is None
            and (domain != "FULL" or current_user.role == models.UserRole.MASTER)
        )
    except Exception:
        restore_point_info = {"id": _restore_point_id_from_summary(summary), "status": "UNAVAILABLE", "file_exists": False}
        rollback_unavailable_reason = "RESTORE_POINT_MISSING"
        rollback_available = False
    summary["restore_point"] = restore_point_info
    summary["rollback_available"] = rollback_available
    summary["is_latest_success_restore"] = is_latest_success_restore
    if rollback_unavailable_reason:
        summary["rollback_unavailable_reason"] = rollback_unavailable_reason
    else:
        summary.pop("rollback_unavailable_reason", None)
    return {
        "id": job.id,
        "backup_id": job.backup_id,
        "domain": job.domain,
        "mode": job.mode,
        "status": job.status,
        "requested_by": job.requested_by,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_message": job.error_message,
        "summary": summary,
    }


async def _read_uploaded_json(file: UploadFile | None) -> dict:
    if file is None:
        raise HTTPException(status_code=400, detail="Backup file is required")
    filename = file.filename or ""
    if not filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Only .json files are allowed")
    max_bytes = get_settings().BACKUP_UPLOAD_MAX_BYTES
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail="Backup file is too large")
    payload, errors = parse_backup_json_bytes(content)
    if payload is None:
        raise HTTPException(status_code=400, detail=errors[0] if errors else "Invalid JSON")
    return payload


@router.get("/backups/storage", response_model=schemas.StorageBackupListOut)
def list_storage_backups(
    domain: str = Query(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    requested_domain = _ensure_domain_access(domain, current_user, action="view")
    try:
        return {"items": list_storage_backup_files(db, requested_domain)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unsupported backup domain") from exc


@router.post("/backups/storage/validate", response_model=schemas.BackupValidationResult)
def validate_storage_backup(
    payload: schemas.StorageBackupValidateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    requested_domain = _ensure_domain_access(payload.domain, current_user, action="validate")
    try:
        result = validate_storage_backup_file(db, domain=requested_domain, storage_key=payload.storage_key)
        return _validation_response(result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backups/storage/register", response_model=schemas.DataBackupOut, status_code=status.HTTP_201_CREATED)
def register_storage_backup(
    payload: schemas.StorageBackupRegisterRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    requested_domain = _ensure_domain_access(payload.domain, current_user, action="register")
    try:
        backup, details = register_storage_backup_file(
            db,
            domain=requested_domain,
            storage_key=payload.storage_key,
            description=payload.description,
            current_user=current_user,
        )
        record_log(
            db,
            actor_id=str(current_user.id),
            action="DATA_BACKUP_STORAGE_REGISTER",
            details={
                "backup_id": str(backup.id),
                "domain": backup.domain,
                "file_name": backup.file_name,
                "display_path": details.get("display_path"),
                "original_created_at": details.get("original_created_at"),
            },
        )
        db.commit()
        db.refresh(backup)
        return backup
    except FileExistsError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/backups/{backup_id}/preview", response_model=schemas.BackupPreviewOut)
def preview_backup(
    backup_id,
    sensitive: bool = Query(False),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    backup = _get_backup_or_404(db, backup_id)
    domain = normalize_domain(backup.domain)
    if domain in {"FULL", "WORK_SYSTEM"} and current_user.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail=f"Only masters can preview {domain} backups")
    if sensitive and current_user.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Only masters can request sensitive preview")
    try:
        preview = build_backup_preview(db, backup=backup, sensitive=sensitive)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if sensitive:
        record_log(
            db,
            actor_id=str(current_user.id),
            action="DATA_BACKUP_PREVIEW_SENSITIVE",
            details={
                "backup_id": str(backup.id),
                "domain": backup.domain,
                "viewer_id": str(current_user.id),
                "user_id": str(current_user.id),
                "sensitive": True,
            },
        )
        db.commit()
    return preview


@router.get("/backups", response_model=list[schemas.DataBackupOut])
def list_backups(
    domain: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    query = db.query(models.DataBackup).filter(
        models.DataBackup.deleted_at.is_(None),
        models.DataBackup.kind != "RESTORE_POINT",
    )
    if domain:
        requested_domain = normalize_domain(domain)
        if requested_domain not in BACKUP_DOMAINS:
            raise HTTPException(status_code=400, detail="Unsupported backup domain")
        if requested_domain in {"FULL", "WORK_SYSTEM"} and current_user.role != models.UserRole.MASTER:
            raise HTTPException(status_code=403, detail=f"Only masters can view {requested_domain} backups")
        query = query.filter(models.DataBackup.domain == requested_domain)
    if current_user.role != models.UserRole.MASTER:
        query = query.filter(models.DataBackup.domain.in_(("VISITORS", "SERIALS", "WORK")))
    backups = query.order_by(models.DataBackup.created_at.desc()).all()
    return [backup for backup in backups if _safe_backup_file_exists(backup)]


@router.post("/backups", response_model=schemas.DataBackupOut, status_code=status.HTTP_201_CREATED)
def create_backup(
    payload: schemas.DataBackupCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    domain = _reject_unsupported_domain(payload.domain)
    if domain in {"FULL", "WORK_SYSTEM"} and current_user.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Only masters can create this backup")
    try:
        backup = create_json_backup(
            db,
            domain=domain,
            current_user=current_user,
            description=payload.description,
        )
        record_log(
            db,
            actor_id=str(current_user.id),
            action={
                "FULL": "DATA_BACKUP_CREATE_FULL",
                "WORK": "DATA_BACKUP_CREATE_WORK",
                "WORK_SYSTEM": "DATA_BACKUP_CREATE_WORK_SYSTEM",
            }.get(domain, "DATA_BACKUP_CREATE"),
            details={"backup_id": str(backup.id), "domain": backup.domain, "file_name": backup.file_name},
        )
        db.commit()
        db.refresh(backup)
        return backup
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Unsupported backup domain") from exc
    except OSError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to write backup file") from exc


@router.post("/backups/upload/validate", response_model=schemas.BackupValidationResult)
async def validate_uploaded_backup(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    payload = await _read_uploaded_json(file)
    result = validate_backup_payload(db, payload, backup_type="JSON")
    return _validation_response(result)


@router.post("/backups/upload/restore", response_model=schemas.DataRestoreJobOut)
async def restore_uploaded_backup_endpoint(
    file: UploadFile = File(...),
    confirm_text: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    if confirm_text != "복원합니다":
        raise HTTPException(status_code=400, detail="Invalid restore confirmation text")
    payload = await _read_uploaded_json(file)
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    upload_domain = normalize_domain(meta.get("backup_type") or meta.get("domain") or "") if isinstance(meta, dict) else ""
    if upload_domain == "WORK_SYSTEM":
        raise HTTPException(status_code=400, detail="WORK_SYSTEM upload restore is not supported")
    validation = validate_backup_payload(db, payload, backup_type="JSON")
    record_log(
        db,
        actor_id=str(current_user.id),
        action="DATA_UPLOAD_RESTORE_START",
        details={"file_name": file.filename, "domain": validation.get("domain")},
    )
    try:
        job, restore_validation = restore_uploaded_backup(db, payload=payload, current_user=current_user, mode="REPLACE")
        if job.status == "SUCCESS":
            record_log(
                db,
                actor_id=str(current_user.id),
                action="DATA_UPLOAD_RESTORE_SUCCESS",
                details={"file_name": file.filename, "domain": job.domain, "job_id": str(job.id)},
            )
        else:
            record_log(
                db,
                actor_id=str(current_user.id),
                action="DATA_UPLOAD_RESTORE_FAILED",
                details={
                    "file_name": file.filename,
                    "domain": validation.get("domain"),
                    "errors": restore_validation.get("errors", []),
                    "job_id": str(job.id),
                },
            )
        db.commit()
        db.refresh(job)
        return job
    except Exception as exc:
        db.rollback()
        failed_job = models.DataRestoreJob(
            backup_id=None,
            domain=validation.get("domain") or "UNKNOWN",
            mode="REPLACE",
            status="FAILED",
            requested_by=current_user.id,
            finished_at=datetime.now(timezone.utc),
            error_message=str(exc),
            summary={"error": str(exc), "source": "UPLOAD"},
        )
        db.add(failed_job)
        record_log(
            db,
            actor_id=str(current_user.id),
            action="DATA_UPLOAD_RESTORE_FAILED",
            details={"file_name": file.filename, "domain": validation.get("domain"), "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status_code=400, detail="Upload restore failed") from exc


@router.get("/backups/{backup_id}/download")
def download_backup(
    backup_id,
    sanitize: bool = Query(False),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    backup = _get_backup_or_404(db, backup_id)
    domain = normalize_domain(backup.domain)
    if backup.kind == "RESTORE_POINT":
        raise HTTPException(status_code=403, detail="Restore points cannot be downloaded")
    if domain in {"FULL", "WORK", "WORK_SYSTEM"}:
        raise HTTPException(status_code=403, detail="This backup domain cannot be downloaded")
    storage_root = Path(get_settings().BACKUP_STORAGE_DIR).resolve()
    file_path = Path(backup.file_path).resolve()
    if file_path != storage_root and storage_root not in file_path.parents:
        raise HTTPException(status_code=403, detail="Backup file is outside storage directory")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Backup file not found")
    if sanitize and domain != "FULL":
        raise HTTPException(status_code=400, detail="Sanitized download is only available for FULL backups")
    if sanitize and domain == "FULL":
        try:
            sanitized_payload = build_sanitized_backup_payload(file_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Unable to sanitize backup file") from exc
        record_log(
            db,
            actor_id=str(current_user.id),
            action="DATA_BACKUP_DOWNLOAD_SANITIZED",
            details={"backup_id": str(backup.id), "domain": backup.domain, "file_name": backup.file_name},
        )
        db.commit()
        content = json.dumps(sanitized_payload, ensure_ascii=False, indent=2).encode("utf-8")
        filename = backup.file_name.replace(".json", "_sanitized.json")
        return StreamingResponse(
            BytesIO(content),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    record_log(
        db,
        actor_id=str(current_user.id),
        action="DATA_BACKUP_DOWNLOAD",
        details={"backup_id": str(backup.id), "domain": backup.domain, "file_name": backup.file_name},
    )
    db.commit()
    return FileResponse(
        path=file_path,
        filename=backup.file_name,
        media_type="application/json",
    )


@router.delete("/backups/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(
    backup_id,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.MASTER)),
):
    backup = _get_backup_or_404(db, backup_id)
    backup.deleted_at = datetime.now(timezone.utc)
    backup.status = "DELETED"
    record_log(
        db,
        actor_id=str(current_user.id),
        action="DATA_BACKUP_DELETE",
        details={"backup_id": str(backup.id), "domain": backup.domain, "file_name": backup.file_name},
    )
    db.commit()
    return None


@router.post("/backups/{backup_id}/validate", response_model=schemas.BackupValidationResult)
def validate_backup(
    backup_id,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    backup = _get_backup_or_404(db, backup_id)
    domain = normalize_domain(backup.domain)
    if domain in {"FULL", "WORK_SYSTEM"} and current_user.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail=f"Only masters can validate {domain} backups")
    result = _validate_registered_backup(db, backup)
    record_log(
        db,
        actor_id=str(current_user.id),
        action="DATA_BACKUP_VALIDATE",
        details={"backup_id": str(backup.id), "domain": backup.domain, "valid": result.get("valid", False)},
    )
    db.commit()
    return _validation_response(result)


@router.post("/backups/{backup_id}/restore", response_model=schemas.DataRestoreJobOut)
def restore_backup_endpoint(
    backup_id,
    payload: schemas.BackupRestoreRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    backup = _get_backup_or_404(db, backup_id)
    mode = (payload.mode or "").strip().upper()
    domain = normalize_domain(backup.domain)
    if domain not in BACKUP_DOMAINS or backup.backup_type != "JSON":
        raise HTTPException(status_code=400, detail="Only JSON backups can be restored")
    if domain == "WORK_SYSTEM":
        if current_user.role != models.UserRole.MASTER:
            raise HTTPException(status_code=403, detail="Only masters can restore WORK_SYSTEM backups")
    if domain == "FULL" and current_user.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Only masters can restore FULL backups")
    if payload.confirm_text != "복원합니다":
        raise HTTPException(status_code=400, detail="Invalid restore confirmation text")
    if mode not in {"DRY_RUN", "REPLACE"}:
        raise HTTPException(status_code=400, detail="Unsupported restore mode")

    record_log(
        db,
        actor_id=_restore_actor_id(domain, current_user),
        action="DATA_RESTORE_START",
        details=_restore_details(
            domain,
            current_user,
            {"backup_id": str(backup.id), "domain": backup.domain, "mode": mode},
        ),
    )
    try:
        job, validation = restore_backup(db, backup=backup, current_user=current_user, mode=mode)
        if job.status == "SUCCESS":
            record_log(
                db,
                actor_id=_restore_actor_id(job.domain, current_user),
                action="DATA_RESTORE_SUCCESS",
                details=_restore_details(
                    job.domain,
                    current_user,
                    {"backup_id": str(backup.id), "domain": job.domain, "mode": job.mode, "job_id": str(job.id)},
                ),
            )
        else:
            record_log(
                db,
                actor_id=_restore_actor_id(backup.domain, current_user),
                action="DATA_RESTORE_FAILED",
                details=_restore_details(
                    backup.domain,
                    current_user,
                    {
                        "backup_id": str(backup.id),
                        "domain": backup.domain,
                        "mode": mode,
                        "errors": validation.get("errors", []),
                        "job_id": str(job.id),
                    },
                ),
            )
        db.commit()
        db.refresh(job)
        return job
    except Exception as exc:
        db.rollback()
        failed_job = models.DataRestoreJob(
            backup_id=backup.id,
            domain=normalize_domain(backup.domain),
            mode=mode,
            status="FAILED",
            requested_by=current_user.id,
            finished_at=datetime.now(timezone.utc),
            error_message=str(exc),
            summary={"error": str(exc)},
        )
        db.add(failed_job)
        record_log(
            db,
            actor_id=_restore_actor_id(backup.domain, current_user),
            action="DATA_RESTORE_FAILED",
            details=_restore_details(
                backup.domain,
                current_user,
                {"backup_id": str(backup.id), "domain": backup.domain, "mode": mode, "error": str(exc)},
            ),
        )
        db.commit()
        raise HTTPException(status_code=400, detail="Restore failed") from exc


@router.post("/restores/{restore_job_id}/rollback", response_model=schemas.DataRestoreJobOut)
def rollback_restore_job(
    restore_job_id,
    payload: schemas.RestoreRollbackRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    restore_job = db.query(models.DataRestoreJob).filter(models.DataRestoreJob.id == restore_job_id).first()
    if not restore_job:
        raise HTTPException(status_code=404, detail="Restore job not found")
    domain = _ensure_domain_access(restore_job.domain, current_user, action="rollback")
    summary = dict(restore_job.summary or {})
    rollback_unavailable_reason = _rollback_unavailable_reason(db, restore_job)
    if rollback_unavailable_reason:
        _raise_rollback_unavailable(rollback_unavailable_reason)
    restore_point_backup = _get_restore_point_backup(db, restore_job)
    if not restore_point_backup:
        _raise_rollback_unavailable("RESTORE_POINT_MISSING")
    if restore_point_backup.kind != "RESTORE_POINT":
        _raise_rollback_unavailable("RESTORE_POINT_MISSING")
    if normalize_domain(restore_point_backup.domain) != domain:
        _raise_rollback_unavailable("RESTORE_POINT_MISSING")
    if not _safe_backup_file_exists(restore_point_backup):
        _raise_rollback_unavailable("RESTORE_POINT_MISSING")
    if payload.confirm_text != "되돌립니다":
        raise HTTPException(status_code=400, detail="Invalid rollback confirmation text")

    try:
        rollback_job, validation = restore_backup(db, backup=restore_point_backup, current_user=current_user, mode="REPLACE")
        rollback_summary = dict(rollback_job.summary or {})
        rollback_summary["rollback_of_restore_job_id"] = str(restore_job.id)
        rollback_job.summary = rollback_summary
        if rollback_job.status == "SUCCESS":
            summary["rollback_used"] = True
            summary["rollback_job_id"] = str(rollback_job.id)
            summary["rollback_at"] = datetime.now(timezone.utc).isoformat()
            restore_job.summary = summary
            record_log(
                db,
                actor_id=_restore_actor_id(domain, current_user),
                action="DATA_RESTORE_ROLLBACK",
                details=_restore_details(
                    domain,
                    current_user,
                    {
                        "restore_job_id": str(restore_job.id),
                        "rollback_job_id": str(rollback_job.id),
                        "restore_point_backup_id": str(restore_point_backup.id),
                        "domain": domain,
                    },
                ),
            )
        else:
            record_log(
                db,
                actor_id=_restore_actor_id(domain, current_user),
                action="DATA_RESTORE_FAILED",
                details=_restore_details(
                    domain,
                    current_user,
                    {
                        "restore_job_id": str(restore_job.id),
                        "rollback_job_id": str(rollback_job.id),
                        "restore_point_backup_id": str(restore_point_backup.id),
                        "domain": domain,
                        "errors": validation.get("errors", []),
                    },
                ),
            )
        db.commit()
        db.refresh(rollback_job)
        return rollback_job
    except Exception as exc:
        db.rollback()
        record_log(
            db,
            actor_id=_restore_actor_id(domain, current_user),
            action="DATA_RESTORE_FAILED",
            details=_restore_details(
                domain,
                current_user,
                {"restore_job_id": str(restore_job.id), "domain": domain, "error": str(exc)},
            ),
        )
        db.commit()
        raise HTTPException(status_code=400, detail="Rollback failed") from exc


@router.get("/restores/reset-cutoff", response_model=schemas.RestoreHistoryResetCutoffOut)
def get_restore_reset_cutoff(
    domain: str = Query(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    requested_domain = _ensure_domain_access(domain, current_user, action="view")
    latest_reset_at, latest_reset_scope, reset_scopes = _latest_reset_cutoff(db, requested_domain)
    return {
        "domain": requested_domain,
        "reset_scopes": list(reset_scopes),
        "latest_reset_at": latest_reset_at,
        "latest_reset_scope": latest_reset_scope,
    }


@router.get("/restores", response_model=list[schemas.DataRestoreJobOut])
def list_restore_jobs(
    domain: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    query = db.query(models.DataRestoreJob)
    if domain:
        requested_domain = normalize_domain(domain)
        if requested_domain not in BACKUP_DOMAINS:
            raise HTTPException(status_code=400, detail="Unsupported backup domain")
        if requested_domain in {"FULL", "WORK_SYSTEM"} and current_user.role != models.UserRole.MASTER:
            raise HTTPException(status_code=403, detail=f"Only masters can view {requested_domain} restore history")
        query = query.filter(models.DataRestoreJob.domain == requested_domain)
    if current_user.role != models.UserRole.MASTER:
        query = query.filter(models.DataRestoreJob.domain.in_(("VISITORS", "SERIALS", "WORK")))
    jobs = query.order_by(models.DataRestoreJob.started_at.desc()).limit(100).all()
    latest_success_restore_job_ids = _latest_success_restore_job_ids_by_domain(db)
    return [
        _restore_job_response(
            db,
            job,
            current_user,
            latest_success_restore_job_ids.get(normalize_domain(job.domain)),
        )
        for job in jobs
    ]


@router.get("/exports/visitors/excel")
def export_visitors_excel(
    year_id: UUID | None = Query(default=None),
    academic_year: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    if year_id is None and academic_year is None:
        raise HTTPException(status_code=400, detail="year_id 또는 academic_year가 필요합니다.")

    year_query = db.query(models.VisitorSchoolYear)
    year = (
        year_query.filter(models.VisitorSchoolYear.id == year_id).first()
        if year_id is not None
        else year_query.filter(models.VisitorSchoolYear.academic_year == academic_year).first()
    )
    if not year:
        raise HTTPException(status_code=404, detail="Visitor academic year not found")

    try:
        path = build_visitors_excel(db, year_id=year.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Visitor academic year not found") from exc
    record_log(
        db,
        actor_id=str(current_user.id),
        action="DATA_EXPORT_VISITORS_EXCEL",
        details={"year_id": str(year.id), "academic_year": year.academic_year},
    )
    db.commit()
    filename = f"{year.academic_year}학년도 참고열람실 출입자 통계.xlsx"
    encoded_filename = quote(filename)
    return FileResponse(
        path=path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        },
        background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
    )


@router.get("/exports/serials/excel")
def export_serials_excel(
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    path = build_serials_excel(db)
    record_log(
        db,
        actor_id=str(current_user.id),
        action="DATA_EXPORT_SERIALS_EXCEL",
        details={},
    )
    db.commit()
    filename = f"serials_report_{date.today().strftime('%Y%m%d')}.xlsx"
    return FileResponse(
        path=path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
    )
