# File: /backend/app/routers/history.py
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import get_db
from ..core.roles import require_role

router = APIRouter(prefix="/history", tags=["history"])


DEFAULT_HISTORY_DAYS = 30
DEFAULT_HISTORY_LIMIT = 50
ALLOWED_HISTORY_DAYS = {"30", "90", "all"}
ALLOWED_HISTORY_LIMITS = {50, 100, 200, 500}

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


def _parse_limit(value: str | int | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_HISTORY_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_HISTORY_LIMIT
    return parsed if parsed in ALLOWED_HISTORY_LIMITS else DEFAULT_HISTORY_LIMIT


def _parse_days(value: str | None) -> str:
    normalized = str(value or DEFAULT_HISTORY_DAYS).strip().lower()
    return normalized if normalized in ALLOWED_HISTORY_DAYS else str(DEFAULT_HISTORY_DAYS)


def _history_cutoff(days: str) -> datetime | None:
    if days == "all":
        return None
    return datetime.now(timezone.utc) - timedelta(days=int(days))


def _entry_from_log(log: models.AuditLog, users: dict) -> schemas.HistoryEntry:
    return schemas.HistoryEntry(
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


@router.get("/stats", response_model=schemas.HistoryStatsOut)
def history_stats(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.MASTER))):
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=DEFAULT_HISTORY_DAYS)
    total_logs = db.query(func.count(models.AuditLog.id)).scalar() or 0
    recent_30_days = (
        db.query(func.count(models.AuditLog.id))
        .filter(models.AuditLog.created_at >= recent_cutoff)
        .scalar()
        or 0
    )
    oldest_log, newest_log = db.query(
        func.min(models.AuditLog.created_at),
        func.max(models.AuditLog.created_at),
    ).one()
    request_linked = db.query(func.count(models.AuditLog.id)).filter(models.AuditLog.request_id.isnot(None)).scalar() or 0
    actor_linked = db.query(func.count(models.AuditLog.id)).filter(models.AuditLog.actor_user_id.isnot(None)).scalar() or 0
    action_counts = (
        db.query(models.AuditLog.action_type, func.count(models.AuditLog.id))
        .group_by(models.AuditLog.action_type)
        .order_by(models.AuditLog.action_type.asc())
        .all()
    )
    return schemas.HistoryStatsOut(
        total_logs=total_logs,
        recent_30_days=recent_30_days,
        display_limit=DEFAULT_HISTORY_LIMIT,
        current_window_days=DEFAULT_HISTORY_DAYS,
        oldest_log=oldest_log,
        newest_log=newest_log,
        request_linked=request_linked,
        request_unlinked=total_logs - request_linked,
        actor_linked=actor_linked,
        actor_missing=total_logs - actor_linked,
        by_action={action_type: count for action_type, count in action_counts},
    )


@router.get("", response_model=list[schemas.HistoryEntry])
def history_logs(
    days: str | None = None,
    limit: str | None = None,
    action_type: str | None = None,
    db: Session = Depends(get_db),
    current=Depends(require_role(models.UserRole.MASTER)),
):
    selected_days = _parse_days(days)
    selected_limit = _parse_limit(limit)
    cutoff = _history_cutoff(selected_days)
    query = db.query(models.AuditLog)
    if cutoff is not None:
        query = query.filter(models.AuditLog.created_at >= cutoff)
    if action_type and action_type.strip():
        query = query.filter(models.AuditLog.action_type == action_type.strip())
    if current.role == models.UserRole.MEMBER:
        query = query.filter((models.AuditLog.actor_user_id == current.id) | (models.AuditLog.target_user_id == current.id))
    logs = query.order_by(models.AuditLog.created_at.desc()).limit(selected_limit).all()
    users = {u.id: u for u in db.query(models.User).all()}
    return [_entry_from_log(log, users) for log in logs]
