# File: /backend/app/routers/system.py
import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..deps import get_db
from .. import models, schemas
from ..core.roles import require_role
from ..core.security import get_password_hash
from ..core.audit import record_log

router = APIRouter(tags=["system"])

NOTICE_RESET_MODELS = (
    models.NoticeRead,
    models.NoticeTarget,
    models.Notice,
)

VISITOR_RESET_MODELS = (
    models.VisitorYearStat,
    models.VisitorPeriodStat,
    models.VisitorMonthlyStat,
    models.VisitorRunningTotal,
    models.VisitorDailyCount,
    models.VisitorPeriod,
    models.VisitorSchoolYear,
)

SERIAL_RESET_MODELS = (
    models.SerialPublication,
    models.SerialShelf,
    models.SerialShelfType,
    models.SerialLayout,
)

WORK_RESET_MODELS = (
    models.ShiftRequest,
    models.UserShift,
    models.Shift,
)

ACCOUNT_RESET_MODELS = (
    models.AuthAccount,
    models.User,
)


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


def _seed_master(db: Session, master: models.User | None = None):
    login_id = os.getenv("MASTER_LOGIN_ID", "master")
    password = os.getenv("MASTER_PASSWORD", "Master123!")
    name = os.getenv("MASTER_NAME", "Master Admin")
    identifier = os.getenv("MASTER_IDENTIFIER", "MASTER_DEFAULT")

    if master is None:
        master = models.User(name=name, identifier=identifier, role=models.UserRole.MASTER)
        db.add(master)
    else:
        master.name = name
        master.identifier = identifier
        master.role = models.UserRole.MASTER
        master.active = True
    db.flush()
    db.add(models.AuthAccount(user_id=master.id, login_id=login_id, password_hash=get_password_hash(password)))
    return master


def _delete_by_roles(db: Session, roles: list[models.UserRole], replacement_user_id) -> int:
    user_ids = [row[0] for row in db.query(models.User.id).filter(models.User.role.in_(roles)).all()]
    if not user_ids:
        return 0

    # 요청/근무/로그를 먼저 정리하여 FK 제약 오류를 방지
    db.query(models.AuditLog).filter(models.AuditLog.actor_user_id.in_(user_ids)).update(
        {models.AuditLog.actor_user_id: None}, synchronize_session=False
    )
    db.query(models.AuditLog).filter(models.AuditLog.target_user_id.in_(user_ids)).update(
        {models.AuditLog.target_user_id: None}, synchronize_session=False
    )

    for model, column in (
        (models.DataBackup, models.DataBackup.created_by),
        (models.DataRestoreJob, models.DataRestoreJob.requested_by),
        (models.VisitorDailyCount, models.VisitorDailyCount.created_by),
        (models.VisitorDailyCount, models.VisitorDailyCount.updated_by),
        (models.SerialLayout, models.SerialLayout.created_by),
        (models.SerialLayout, models.SerialLayout.updated_by),
        (models.SerialShelfType, models.SerialShelfType.created_by),
        (models.SerialShelfType, models.SerialShelfType.updated_by),
        (models.SerialShelf, models.SerialShelf.created_by),
        (models.SerialShelf, models.SerialShelf.updated_by),
        (models.SerialPublication, models.SerialPublication.created_by),
        (models.SerialPublication, models.SerialPublication.updated_by),
    ):
        db.query(model).filter(column.in_(user_ids)).update({column: None}, synchronize_session=False)

    shift_request_ids = [
        row[0] for row in db.query(models.ShiftRequest.id).filter(models.ShiftRequest.user_id.in_(user_ids)).all()
    ]
    if shift_request_ids:
        db.query(models.AuditLog).filter(models.AuditLog.request_id.in_(shift_request_ids)).update(
            {models.AuditLog.request_id: None}, synchronize_session=False
        )
    db.query(models.Notice).filter(models.Notice.created_by.in_(user_ids)).update(
        {models.Notice.created_by: replacement_user_id}, synchronize_session=False
    )

    db.query(models.ShiftRequest).filter(models.ShiftRequest.operator_id.in_(user_ids)).update(
        {models.ShiftRequest.operator_id: None}, synchronize_session=False
    )
    db.query(models.ShiftRequest).filter(models.ShiftRequest.user_id.in_(user_ids)).delete(synchronize_session=False)
    db.query(models.UserShift).filter(models.UserShift.user_id.in_(user_ids)).delete(synchronize_session=False)
    db.query(models.AuthAccount).filter(models.AuthAccount.user_id.in_(user_ids)).delete(synchronize_session=False)
    deleted = db.query(models.User).filter(models.User.id.in_(user_ids)).delete(synchronize_session=False)
    return deleted


def _delete_domain_models(db: Session, reset_models: tuple[type[models.Base], ...]) -> int:
    deleted = 0
    for model in reset_models:
        deleted += db.query(model).delete(synchronize_session=False)
    return deleted


@router.post("/reset", response_model=dict)
def reset_data(payload: schemas.ResetRequest, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    scope = payload.scope

    if scope in (schemas.ResetScope.OPERATORS_AND_MEMBERS, schemas.ResetScope.ALL) and current.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Only masters can perform this reset")

    actor_id_for_log: str | None = str(current.id)
    if scope == schemas.ResetScope.MEMBERS:
        removed = _delete_by_roles(db, [models.UserRole.MEMBER], current.id)
        detail = f"근무 운영 초기화 완료 ({removed}명)"
    elif scope == schemas.ResetScope.OPERATORS_AND_MEMBERS:
        removed = _delete_by_roles(db, [models.UserRole.MEMBER, models.UserRole.OPERATOR], current.id)
        detail = f"근무 시스템 초기화 완료 ({removed}명)"
    elif scope == schemas.ResetScope.VISITORS_ALL:
        removed = _delete_domain_models(db, VISITOR_RESET_MODELS)
        detail = f"출입 전체 초기화 완료 ({removed}건)"
    elif scope == schemas.ResetScope.SERIALS_ALL:
        removed = _delete_domain_models(db, SERIAL_RESET_MODELS)
        detail = f"연속간행물 전체 초기화 완료 ({removed}건)"
    else:
        # 전체 초기화 시에는 현재 계정도 삭제되므로 로그에 배우자 ID만 남기고 actor_id는 비워 FK 오류를 방지
        actor_id_for_log = None
        performed_by = str(current.id)
        db.query(models.AuditLog).update(
            {
                models.AuditLog.actor_user_id: None,
                models.AuditLog.target_user_id: None,
                models.AuditLog.request_id: None,
            },
            synchronize_session=False,
        )
        db.query(models.DataBackup).update({models.DataBackup.created_by: None}, synchronize_session=False)
        db.query(models.DataRestoreJob).update({models.DataRestoreJob.requested_by: None}, synchronize_session=False)
        _delete_domain_models(db, NOTICE_RESET_MODELS)
        _delete_domain_models(db, VISITOR_RESET_MODELS)
        _delete_domain_models(db, SERIAL_RESET_MODELS)
        _delete_domain_models(db, WORK_RESET_MODELS)
        _delete_domain_models(db, ACCOUNT_RESET_MODELS)
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
