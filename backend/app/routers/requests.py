# File: /backend/app/routers/requests.py
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import schemas, models
from ..deps import get_db
from ..core.roles import require_role
from ..core.audit import record_log
from ..services.schedule_calc import week_events

router = APIRouter(prefix="/requests", tags=["requests"])


def _time_window_from_range(start_hour: int | None, end_hour: int | None):
    if start_hour is None or end_hour is None:
        return None, None
    if start_hour >= end_hour:
        raise HTTPException(status_code=400, detail="시간 범위가 올바르지 않습니다")
    return datetime.strptime(f"{start_hour:02d}:00", "%H:%M").time(), datetime.strptime(f"{end_hour:02d}:00", "%H:%M").time()


def _overlaps(a_start, a_end, b_start, b_end):
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return True
    return a_start < b_end and b_start < a_end


def _week_start(d):
    return d - timedelta(days=d.weekday())


def _assert_same_weekday(target_date, shift):
    if shift.weekday != target_date.weekday():
        raise HTTPException(status_code=400, detail="선택한 날짜와 슬롯의 요일이 일치하지 않습니다")


def _time_window_from_range(start_hour: int | None, end_hour: int | None):
    if start_hour is None or end_hour is None:
        return None, None
    if start_hour >= end_hour:
        raise HTTPException(status_code=400, detail="시간 범위가 올바르지 않습니다")
    return datetime.strptime(f"{start_hour:02d}:00", "%H:%M").time(), datetime.strptime(f"{end_hour:02d}:00", "%H:%M").time()


def _overlaps(a_start, a_end, b_start, b_end):
    # None means full range
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return True
    return a_start < b_end and b_start < a_end


@router.post("", response_model=list[schemas.RequestOut], status_code=status.HTTP_201_CREATED)
def submit_request(payload: schemas.RequestCreate, current=Depends(require_role(models.UserRole.MEMBER)), db: Session = Depends(get_db)):
    target_user_id = payload.user_id or current.id
    target_user = db.query(models.User).filter(models.User.id == target_user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="신청 대상 사용자를 찾을 수 없습니다")
    if target_user.role != models.UserRole.MEMBER:
        raise HTTPException(status_code=400, detail="마스터/운영자는 근무 변경 신청 대상에서 제외됩니다")
    if current.role == models.UserRole.OPERATOR and target_user.role == models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="운영자는 마스터 대신 신청할 수 없습니다")
    if current.role == models.UserRole.MEMBER and target_user_id != current.id:
        raise HTTPException(status_code=403, detail="구성원은 본인 계정으로만 신청할 수 있습니다")

    shift_ids = payload.target_shift_ids or ([payload.target_shift_id] if payload.target_shift_id else [])
    ranges = payload.target_ranges or []
    if not ranges and shift_ids:
        ranges = [schemas.RequestRange(shift_id=sid) for sid in shift_ids]
    if not ranges:
        raise HTTPException(status_code=400, detail="선택된 시간 구간이 없습니다")

    # 현재 주간 일정(결재 반영 포함)을 로드하여 결근/추가 가능 여부 판단
    week_start = _week_start(payload.target_date)
    events = week_events(db, week_start, str(target_user_id))
    effective_slots = {(str(ev.shift_id), ev.date): ev for ev in events}

    created_requests: list[models.ShiftRequest] = []
    for r in ranges:
        sid = r.shift_id
        shift = db.query(models.Shift).filter(models.Shift.id == sid).first()
        if not shift:
            raise HTTPException(status_code=404, detail="선택한 시간 슬롯 정보를 찾을 수 없습니다")
        _assert_same_weekday(payload.target_date, shift)

        start_time, end_time = _time_window_from_range(r.start_hour, r.end_hour)
        if payload.type == models.RequestType.ABSENCE and start_time and end_time:
            if start_time < shift.start_time or end_time > shift.end_time:
                raise HTTPException(status_code=400, detail="선택 시간이 배정된 시간 범위를 벗어났습니다")

        dup_query = db.query(models.ShiftRequest).filter(
            models.ShiftRequest.user_id == target_user_id,
            models.ShiftRequest.target_date == payload.target_date,
            models.ShiftRequest.target_shift_id == sid,
            models.ShiftRequest.status == models.RequestStatus.PENDING,
        )
        for dup in dup_query:
            if _overlaps(start_time, end_time, dup.target_start_time, dup.target_end_time):
                raise HTTPException(status_code=409, detail="이미 동일한 시간에 대기 중인 신청이 있습니다")

        key = (str(sid), payload.target_date)
        has_slot = key in effective_slots
        if payload.type == models.RequestType.ABSENCE and not has_slot:
            raise HTTPException(status_code=400, detail="결근 신청은 현재 배정된 시간에서만 가능합니다")
        if payload.type == models.RequestType.EXTRA and has_slot:
            raise HTTPException(status_code=400, detail="이미 배정된 시간에는 추가 근무를 신청할 수 없습니다")

        req = models.ShiftRequest(
            user_id=target_user_id,
            type=payload.type,
            target_date=payload.target_date,
            target_shift_id=sid,
            target_start_time=start_time,
            target_end_time=end_time,
            reason=payload.reason,
        )
        db.add(req)
        created_requests.append(req)
    db.commit()
    for req in created_requests:
        db.refresh(req)
        record_log(
            db,
            actor_id=str(current.id),
            action="REQUEST_SUBMIT",
            target_user_id=str(target_user_id),
            request_id=str(req.id),
            details={"type": req.type.value, "date": req.target_date.isoformat()},
        )
    db.commit()
    return created_requests


@router.get("/my", response_model=list[schemas.RequestOut])
def my_requests(
    user_id: str | None = None,
    current=Depends(require_role(models.UserRole.MEMBER)),
    db: Session = Depends(get_db),
):
    target_id = user_id or str(current.id)
    if current.role == models.UserRole.MEMBER and target_id != str(current.id):
        raise HTTPException(status_code=403, detail="다른 사용자의 신청 내역을 조회할 수 없습니다")

    target_user = db.query(models.User).filter(models.User.id == target_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="신청 대상 사용자를 찾을 수 없습니다")
    return (
        db.query(models.ShiftRequest)
        .filter(models.ShiftRequest.user_id == target_id)
        .order_by(models.ShiftRequest.created_at.desc())
        .all()
    )


@router.get("/pending", response_model=list[schemas.RequestOut])
def pending_requests(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    return (
        db.query(models.ShiftRequest)
        .filter(
            (models.ShiftRequest.status == models.RequestStatus.PENDING)
            | (
                (models.ShiftRequest.status == models.RequestStatus.CANCELLED)
                & (models.ShiftRequest.cancelled_after_approval == True)
            )
        )
        .order_by(models.ShiftRequest.created_at.desc())
        .all()
    )


@router.get("/feed", response_model=list[schemas.RequestFeedEntry])
def request_feed(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    logs = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.action_type.in_(
            ["REQUEST_SUBMIT", "REQUEST_APPROVE", "REQUEST_REJECT", "REQUEST_CANCEL"]
        ))
        .filter(models.AuditLog.request_id.isnot(None))
        .order_by(models.AuditLog.created_at.desc())
        .limit(50)
        .all()
    )
    if not logs:
        return []
    request_ids = [log.request_id for log in logs if log.request_id]
    requests = (
        db.query(models.ShiftRequest)
        .filter(models.ShiftRequest.id.in_(request_ids))
        .all()
    )
    request_map = {req.id: req for req in requests}
    entries: list[schemas.RequestFeedEntry] = []
    for log in logs:
        req = request_map.get(log.request_id)
        if not req:
            continue
        entries.append(
            schemas.RequestFeedEntry(
                request_id=req.id,
                action_type=log.action_type,
                created_at=log.created_at,
                user_id=req.user_id,
                type=req.type,
                target_date=req.target_date,
                target_shift_id=req.target_shift_id,
                target_start_time=req.target_start_time,
                target_end_time=req.target_end_time,
                reason=req.reason,
                cancelled_after_approval=req.cancelled_after_approval,
                cancel_reason=req.cancel_reason,
            )
        )
    return entries


@router.post("/{request_id}/cancel", response_model=schemas.RequestOut)
def cancel_request(request_id: str, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.MEMBER))):
    req = db.query(models.ShiftRequest).filter(models.ShiftRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="신청 건을 찾을 수 없습니다")
    if current.role == models.UserRole.MEMBER and req.user_id != current.id:
        raise HTTPException(status_code=403, detail="본인 신청만 취소할 수 있습니다")
    if req.status not in (models.RequestStatus.PENDING, models.RequestStatus.APPROVED):
        raise HTTPException(status_code=400, detail="대기 또는 승인된 건만 취소할 수 있습니다")

    was_approved = req.status == models.RequestStatus.APPROVED
    req.status = models.RequestStatus.CANCELLED
    req.cancel_reason = "USER_CANCEL"
    if was_approved:
        req.cancelled_after_approval = True
    req.decided_at = datetime.utcnow()
    req.operator_id = current.id
    db.commit()
    db.refresh(req)
    record_log(
        db,
        actor_id=str(current.id),
        action="REQUEST_CANCEL",
        target_user_id=str(req.user_id),
        request_id=str(req.id),
        details={"type": req.type.value, "cancel_reason": req.cancel_reason, "cancelled_after_approval": req.cancelled_after_approval},
    )
    db.commit()
    return req


@router.post("/{request_id}/approve", response_model=schemas.RequestOut)
def approve_request(request_id: str, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    req = db.query(models.ShiftRequest).filter(models.ShiftRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="신청 건을 찾을 수 없습니다")
    if req.status == models.RequestStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="취소된 신청은 승인할 수 없습니다")
    if req.status == models.RequestStatus.APPROVED:
        raise HTTPException(status_code=400, detail="이미 승인된 신청입니다")
    req.status = models.RequestStatus.APPROVED
    req.operator_id = current.id
    req.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(req)
    record_log(
        db,
        actor_id=str(current.id),
        action="REQUEST_APPROVE",
        target_user_id=str(req.user_id),
        request_id=str(req.id),
        details={"type": req.type.value},
    )
    db.commit()
    return req


@router.post("/{request_id}/reject", response_model=schemas.RequestOut)
def reject_request(request_id: str, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    req = db.query(models.ShiftRequest).filter(models.ShiftRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="신청 건을 찾을 수 없습니다")
    if req.status in (models.RequestStatus.CANCELLED, models.RequestStatus.REJECTED):
        raise HTTPException(status_code=400, detail="이미 처리된 신청입니다")
    req.status = models.RequestStatus.REJECTED
    req.cancel_reason = "REJECTED"
    req.operator_id = current.id
    req.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(req)
    record_log(
        db,
        actor_id=str(current.id),
        action="REQUEST_REJECT",
        target_user_id=str(req.user_id),
        request_id=str(req.id),
        details={"type": req.type.value, "cancel_reason": "REJECTED"},
    )
    db.commit()
    return req
