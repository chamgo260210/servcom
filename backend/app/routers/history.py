# File: /backend/app/routers/history.py
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import get_db
from ..core.roles import require_role

router = APIRouter(prefix="/history", tags=["history"])


ACTION_LABEL = {
    "REQUEST_SUBMIT": "신청 접수",
    "REQUEST_APPROVE": "신청 승인",
    "REQUEST_REJECT": "신청 거절",
    "REQUEST_CANCEL": "신청 취소",
    "ASSIGN_SLOT": "근무 배정",
    "USER_CREATE": "사용자 생성",
    "USER_UPDATE": "사용자 수정",
    "CREDENTIAL_UPDATE": "자격 변경",
    "USER_DELETE": "사용자 삭제",
    "RESET_DATA": "데이터 초기화",
    "NOTICE_CREATE": "공지 생성",
    "NOTICE_UPDATE": "공지 수정",
    "NOTICE_DELETE": "공지 삭제",
}


@router.get("", response_model=list[schemas.HistoryEntry])
def history_logs(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.MASTER))):
    cutoff = datetime.utcnow() - timedelta(days=30)
    query = db.query(models.AuditLog).filter(models.AuditLog.created_at >= cutoff)
    if current.role == models.UserRole.MEMBER:
        query = query.filter((models.AuditLog.actor_user_id == current.id) | (models.AuditLog.target_user_id == current.id))
    logs = query.order_by(models.AuditLog.created_at.desc()).limit(50).all()
    users = {u.id: u for u in db.query(models.User).all()}
    entries: list[schemas.HistoryEntry] = []
    for log in logs:
        entries.append(
            schemas.HistoryEntry(
                id=log.id,
                action_type=log.action_type,
                action_label=ACTION_LABEL.get(log.action_type, log.action_type),
                actor_user_id=log.actor_user_id,
                actor_name=users.get(log.actor_user_id).name if log.actor_user_id in users else None,
                target_user_id=log.target_user_id,
                target_name=users.get(log.target_user_id).name if log.target_user_id in users else None,
                request_id=log.request_id,
                details=log.details,
                created_at=log.created_at,
            )
        )
    return entries
