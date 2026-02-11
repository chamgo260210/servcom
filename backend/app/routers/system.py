# File: /backend/app/routers/system.py
import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from ..deps import get_db
from .. import models, schemas
from ..core.roles import require_role
from ..core.security import get_password_hash
from ..core.audit import record_log

router = APIRouter(tags=["system"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """간단한 DB 연결 헬스체크 엔드포인트."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - runtime safety
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database_unavailable",
        ) from exc
    return {"db_status": "ok"}


def _seed_master(db: Session):
    login_id = os.getenv("MASTER_LOGIN_ID", "master")
    password = os.getenv("MASTER_PASSWORD", "Master123!")
    name = os.getenv("MASTER_NAME", "Master Admin")
    identifier = os.getenv("MASTER_IDENTIFIER", "MASTER_DEFAULT")

    master = models.User(name=name, identifier=identifier, role=models.UserRole.MASTER)
    db.add(master)
    db.flush()
    db.add(models.AuthAccount(user_id=master.id, login_id=login_id, password_hash=get_password_hash(password)))
    return master


def _delete_by_roles(db: Session, roles: list[models.UserRole]) -> int:
    user_ids = [row[0] for row in db.query(models.User.id).filter(models.User.role.in_(roles)).all()]
    if not user_ids:
        return 0

    # 요청/근무/로그를 먼저 정리하여 FK 제약 오류를 방지
    db.query(models.AuditLog).filter(
        or_(models.AuditLog.actor_user_id.in_(user_ids), models.AuditLog.target_user_id.in_(user_ids))
    ).delete(synchronize_session=False)

    db.query(models.ShiftRequest).filter(models.ShiftRequest.operator_id.in_(user_ids)).update(
        {models.ShiftRequest.operator_id: None}, synchronize_session=False
    )
    db.query(models.ShiftRequest).filter(models.ShiftRequest.user_id.in_(user_ids)).delete(synchronize_session=False)
    db.query(models.UserShift).filter(models.UserShift.user_id.in_(user_ids)).delete(synchronize_session=False)
    db.query(models.AuthAccount).filter(models.AuthAccount.user_id.in_(user_ids)).delete(synchronize_session=False)
    deleted = db.query(models.User).filter(models.User.id.in_(user_ids)).delete(synchronize_session=False)
    return deleted


@router.post("/reset", response_model=dict)
def reset_data(payload: schemas.ResetRequest, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    scope = payload.scope

    if scope in (schemas.ResetScope.OPERATORS_AND_MEMBERS, schemas.ResetScope.ALL) and current.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Only masters can perform this reset")

    actor_id_for_log: str | None = str(current.id)
    if scope == schemas.ResetScope.MEMBERS:
        removed = _delete_by_roles(db, [models.UserRole.MEMBER])
        detail = f"Members removed ({removed}명)"
    elif scope == schemas.ResetScope.OPERATORS_AND_MEMBERS:
        removed = _delete_by_roles(db, [models.UserRole.MEMBER, models.UserRole.OPERATOR])
        detail = f"Operators and members removed ({removed}명)"
    else:
        # 전체 초기화 시에는 현재 계정도 삭제되므로 로그에 배우자 ID만 남기고 actor_id는 비워 FK 오류를 방지
        actor_id_for_log = None
        performed_by = str(current.id)
        db.execute(text("TRUNCATE audit_logs, shift_requests, user_shifts, shifts, auth_accounts, users RESTART IDENTITY CASCADE"))
        _seed_master(db)
        detail = "All data cleared and master reseeded"
        # 세부 정보에 실제 실행 주체를 남겨둔다.
        db.commit()
        record_log(
            db,
            actor_id=actor_id_for_log,
            action="RESET_DATA",
            details={"scope": scope.value, "performed_by": performed_by},
        )
        db.commit()
        return {"detail": detail, "scope": scope.value}

    db.commit()
    record_log(
        db,
        actor_id=actor_id_for_log,
        action="RESET_DATA",
        details={"scope": scope.value},
    )
    db.commit()
    return {"detail": detail, "scope": scope.value}
