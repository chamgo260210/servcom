# File: /backend/app/routers/admin.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import schemas, models
from ..deps import get_db
from ..core.roles import require_role

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/audit-logs", response_model=list[schemas.AuditLogOut])
def audit_logs(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.MASTER))):
    return db.query(models.AuditLog).order_by(models.AuditLog.created_at.desc()).limit(200).all()
