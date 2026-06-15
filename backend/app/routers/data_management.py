from __future__ import annotations

import json
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import models, schemas
from ..core.audit import record_log
from ..core.backup_manifest import BACKUP_DOMAINS, PHASE1_REJECTED_DOMAINS
from ..config import get_settings
from ..deps import get_db
from ..core.roles import require_role
from ..services.backup_service import build_sanitized_backup_payload, create_json_backup, normalize_domain
from ..services.excel_export_service import build_serials_excel, build_visitors_excel
from ..services.restore_service import (
    parse_backup_json_bytes,
    restore_backup,
    restore_uploaded_backup,
    validate_backup_file,
    validate_backup_payload,
)

router = APIRouter(prefix="/data", tags=["data-management"])


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
        if requested_domain == "FULL" and current_user.role != models.UserRole.MASTER:
            raise HTTPException(status_code=403, detail="Only masters can view FULL backups")
        query = query.filter(models.DataBackup.domain == requested_domain)
    if current_user.role != models.UserRole.MASTER:
        query = query.filter(models.DataBackup.domain.in_(("VISITORS", "SERIALS", "WORK")))
    return query.order_by(models.DataBackup.created_at.desc()).all()


@router.post("/backups", response_model=schemas.DataBackupOut, status_code=status.HTTP_201_CREATED)
def create_backup(
    payload: schemas.DataBackupCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    domain = _reject_unsupported_domain(payload.domain)
    if domain == "FULL" and current_user.role != models.UserRole.MASTER:
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
            action={"FULL": "DATA_BACKUP_CREATE_FULL", "WORK": "DATA_BACKUP_CREATE_WORK"}.get(domain, "DATA_BACKUP_CREATE"),
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
    if domain in {"FULL", "WORK"}:
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
    if domain == "FULL" and current_user.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Only masters can validate FULL backups")
    result = validate_backup_file(db, backup)
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
    if domain == "FULL" and current_user.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Only masters can restore FULL backups")
    if payload.confirm_text != "복원합니다":
        raise HTTPException(status_code=400, detail="Invalid restore confirmation text")
    if mode not in {"DRY_RUN", "REPLACE"}:
        raise HTTPException(status_code=400, detail="Unsupported restore mode")

    record_log(
        db,
        actor_id=str(current_user.id),
        action="DATA_RESTORE_START",
        details={"backup_id": str(backup.id), "domain": backup.domain, "mode": mode},
    )
    try:
        job, validation = restore_backup(db, backup=backup, current_user=current_user, mode=mode)
        if job.status == "SUCCESS":
            record_log(
                db,
                actor_id=str(current_user.id),
                action="DATA_RESTORE_SUCCESS",
                details={"backup_id": str(backup.id), "domain": job.domain, "mode": job.mode, "job_id": str(job.id)},
            )
        else:
            record_log(
                db,
                actor_id=str(current_user.id),
                action="DATA_RESTORE_FAILED",
                details={
                    "backup_id": str(backup.id),
                    "domain": backup.domain,
                    "mode": mode,
                    "errors": validation.get("errors", []),
                    "job_id": str(job.id),
                },
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
            actor_id=str(current_user.id),
            action="DATA_RESTORE_FAILED",
            details={"backup_id": str(backup.id), "domain": backup.domain, "mode": mode, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status_code=400, detail="Restore failed") from exc


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
        if requested_domain == "FULL" and current_user.role != models.UserRole.MASTER:
            raise HTTPException(status_code=403, detail="Only masters can view FULL restore history")
        query = query.filter(models.DataRestoreJob.domain == requested_domain)
    if current_user.role != models.UserRole.MASTER:
        query = query.filter(models.DataRestoreJob.domain.in_(("VISITORS", "SERIALS", "WORK")))
    return query.order_by(models.DataRestoreJob.started_at.desc()).limit(100).all()


@router.get("/exports/visitors/excel")
def export_visitors_excel(
    academic_year: int = Query(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    try:
        path = build_visitors_excel(db, academic_year)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Visitor academic year not found") from exc
    record_log(
        db,
        actor_id=str(current_user.id),
        action="DATA_EXPORT_VISITORS_EXCEL",
        details={"academic_year": academic_year},
    )
    db.commit()
    filename = f"visitors_{academic_year}_report_{date.today().strftime('%Y%m%d')}.xlsx"
    return FileResponse(
        path=path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
