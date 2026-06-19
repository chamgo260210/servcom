# File: /backend/app/routers/requests.py
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import schemas, models
from ..deps import get_db
from ..core.roles import require_role
from ..core.audit import record_log
from ..services.schedule_calc import week_events

router = APIRouter(prefix="/requests", tags=["requests"])

SEOUL_TZ = ZoneInfo("Asia/Seoul")
PAST_REQUEST_CREATE_MESSAGE = "오늘 이전 날짜에는 근무 변경 신청을 할 수 없습니다."
PAST_APPROVED_CANCEL_MESSAGE = "이미 지난 근무일의 승인 신청은 취소할 수 없습니다."
PAST_DECISION_MESSAGE = "이미 지난 근무일의 신청은 승인/거절할 수 없습니다."
PAST_REOPEN_MESSAGE = "이미 지난 근무일의 신청은 재검토 상태로 되돌릴 수 없습니다."
REQUEST_FEED_ACTIONS = ["REQUEST_SUBMIT", "REQUEST_APPROVE", "REQUEST_REJECT", "REQUEST_CANCEL", "REQUEST_REOPEN"]


def _today_seoul():
    return datetime.now(SEOUL_TZ).date()


def _is_past_target(target_date):
    return target_date < _today_seoul()


def _record_request_cancel_log(db: Session, req: models.ShiftRequest, actor_id: str | None):
    record_log(
        db,
        actor_id=str(actor_id) if actor_id else None,
        action="REQUEST_CANCEL",
        target_user_id=str(req.user_id),
        request_id=str(req.id),
        details={
            "type": req.type.value,
            "cancel_reason": req.cancel_reason,
            "cancelled_after_approval": req.cancelled_after_approval,
        },
    )


def _record_request_reopen_log(db: Session, req: models.ShiftRequest, actor_id: str | None, from_status: str):
    record_log(
        db,
        actor_id=str(actor_id) if actor_id else None,
        action="REQUEST_REOPEN",
        target_user_id=str(req.user_id),
        request_id=str(req.id),
        details={
            "from_status": from_status,
            "to_status": models.RequestStatus.PENDING.value,
        },
    )


def _expire_pending_request(db: Session, req: models.ShiftRequest, actor_id: str | None = None) -> bool:
    if req.status != models.RequestStatus.PENDING or not _is_past_target(req.target_date):
        return False
    req.status = models.RequestStatus.CANCELLED
    req.cancel_reason = "EXPIRED"
    req.cancelled_after_approval = False
    req.decided_at = datetime.now(timezone.utc)
    req.operator_id = None
    _record_request_cancel_log(db, req, actor_id)
    return True


def _expire_pending_requests(db: Session, actor_id: str | None = None, user_id: str | None = None) -> int:
    query = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.status == models.RequestStatus.PENDING,
        models.ShiftRequest.target_date < _today_seoul(),
    )
    if user_id:
        query = query.filter(models.ShiftRequest.user_id == user_id)
    count = 0
    for req in query.all():
        if _expire_pending_request(db, req, actor_id):
            count += 1
    if count:
        db.commit()
    return count


def _time_window_from_range(start_hour: int | None, end_hour: int | None):
    if start_hour is None or end_hour is None:
        return None, None
    if start_hour >= end_hour:
        raise HTTPException(status_code=400, detail="시간 범위가 올바르지 않습니다")
    return datetime.strptime(f"{start_hour:02d}:00", "%H:%M").time(), datetime.strptime(f"{end_hour:02d}:00", "%H:%M").time()


def _effective_window(start_time, end_time, shift: models.Shift):
    return start_time or shift.start_time, end_time or shift.end_time


def _contains(container_start, container_end, child_start, child_end) -> bool:
    return container_start <= child_start and child_end <= container_end


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _event_overlaps(ev: schemas.ScheduleEvent, target_date, start_time, end_time) -> bool:
    return ev.date == target_date and _overlaps(start_time, end_time, ev.start_time, ev.end_time)


def _event_contains(ev: schemas.ScheduleEvent, target_date, start_time, end_time) -> bool:
    return ev.date == target_date and _contains(ev.start_time, ev.end_time, start_time, end_time)


def _week_start(d):
    return d - timedelta(days=d.weekday())


def _assert_same_weekday(target_date, shift):
    if shift.weekday != target_date.weekday():
        raise HTTPException(status_code=400, detail="선택한 날짜와 슬롯의 요일이 일치하지 않습니다")


def _assert_no_pending_overlap(
    db: Session,
    *,
    target_user_id,
    target_date,
    start_time,
    end_time,
) -> None:
    pending_requests = (
        db.query(models.ShiftRequest)
        .filter(
            models.ShiftRequest.user_id == target_user_id,
            models.ShiftRequest.target_date == target_date,
            models.ShiftRequest.status == models.RequestStatus.PENDING,
        )
        .all()
    )
    shift_ids = {req.target_shift_id for req in pending_requests if req.target_shift_id}
    shifts = {shift.id: shift for shift in db.query(models.Shift).filter(models.Shift.id.in_(shift_ids)).all()} if shift_ids else {}
    for req in pending_requests:
        shift = shifts.get(req.target_shift_id)
        if not shift:
            continue
        req_start, req_end = _effective_window(req.target_start_time, req.target_end_time, shift)
        if _overlaps(start_time, end_time, req_start, req_end):
            raise HTTPException(status_code=409, detail="이미 동일한 시간대에 대기 중인 신청이 있습니다")


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
    if _is_past_target(payload.target_date):
        raise HTTPException(status_code=400, detail=PAST_REQUEST_CREATE_MESSAGE)

    shift_ids = payload.target_shift_ids or ([payload.target_shift_id] if payload.target_shift_id else [])
    ranges = payload.target_ranges or []
    if not ranges and shift_ids:
        ranges = [schemas.RequestRange(shift_id=sid) for sid in shift_ids]
    if not ranges:
        raise HTTPException(status_code=400, detail="선택된 시간 구간이 없습니다")

    # 현재 주간 일정(결재 반영 포함)을 로드하여 결근/추가 가능 여부 판단
    week_start = _week_start(payload.target_date)
    events = week_events(db, week_start, str(target_user_id))

    created_requests: list[models.ShiftRequest] = []
    requested_windows: list[tuple] = []
    for r in ranges:
        sid = r.shift_id
        shift = db.query(models.Shift).filter(models.Shift.id == sid).first()
        if not shift:
            raise HTTPException(status_code=404, detail="선택한 시간 슬롯 정보를 찾을 수 없습니다")
        _assert_same_weekday(payload.target_date, shift)

        raw_start_time, raw_end_time = _time_window_from_range(r.start_hour, r.end_hour)
        start_time, end_time = _effective_window(raw_start_time, raw_end_time, shift)
        if start_time < shift.start_time or end_time > shift.end_time:
            raise HTTPException(status_code=400, detail="선택 시간이 배정된 시간 범위를 벗어났습니다")

        for existing_start, existing_end in requested_windows:
            if _overlaps(start_time, end_time, existing_start, existing_end):
                raise HTTPException(status_code=400, detail="신청 시간 구간이 서로 겹칩니다")
        requested_windows.append((start_time, end_time))

        _assert_no_pending_overlap(
            db,
            target_user_id=target_user_id,
            target_date=payload.target_date,
            start_time=start_time,
            end_time=end_time,
        )

        working_events = [ev for ev in events if ev.date == payload.target_date]
        if payload.type == models.RequestType.ABSENCE:
            if not any(_event_contains(ev, payload.target_date, start_time, end_time) for ev in working_events):
                raise HTTPException(status_code=400, detail="결근 신청은 현재 근무 중인 시간 범위 안에서만 가능합니다")
        elif payload.type == models.RequestType.EXTRA:
            if any(_event_overlaps(ev, payload.target_date, start_time, end_time) for ev in working_events):
                raise HTTPException(status_code=400, detail="이미 근무가 있는 시간에는 추가 근무를 신청할 수 없습니다")

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
    _expire_pending_requests(db, user_id=target_id)
    return (
        db.query(models.ShiftRequest)
        .filter(models.ShiftRequest.user_id == target_id)
        .order_by(models.ShiftRequest.created_at.desc())
        .all()
    )


@router.get("/pending", response_model=list[schemas.RequestOut])
def pending_requests(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    _expire_pending_requests(db, actor_id=str(current.id))
    return (
        db.query(models.ShiftRequest)
        .filter(models.ShiftRequest.status == models.RequestStatus.PENDING)
        .order_by(models.ShiftRequest.created_at.desc())
        .all()
    )


def _request_status_action_type(req: models.ShiftRequest) -> str:
    if req.status == models.RequestStatus.APPROVED:
        return "REQUEST_APPROVE"
    if req.status == models.RequestStatus.REJECTED:
        return "REQUEST_REJECT"
    if req.status == models.RequestStatus.CANCELLED:
        return "REQUEST_CANCEL"
    return "REQUEST_SUBMIT"


def _request_feed_entry(
    req: models.ShiftRequest,
    *,
    action_type: str,
    created_at,
    cancel_reason: str | None = None,
) -> schemas.RequestFeedEntry:
    return schemas.RequestFeedEntry(
        request_id=req.id,
        action_type=action_type,
        created_at=created_at,
        user_id=req.user_id,
        status=req.status,
        type=req.type,
        target_date=req.target_date,
        target_shift_id=req.target_shift_id,
        target_start_time=req.target_start_time,
        target_end_time=req.target_end_time,
        reason=req.reason,
        cancelled_after_approval=req.cancelled_after_approval,
        cancel_reason=cancel_reason if cancel_reason is not None else req.cancel_reason,
    )


@router.get("/feed", response_model=list[schemas.RequestFeedEntry])
def request_feed(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    _expire_pending_requests(db, actor_id=str(current.id))
    logs = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.action_type.in_(REQUEST_FEED_ACTIONS))
        .filter(models.AuditLog.request_id.isnot(None))
        .order_by(models.AuditLog.created_at.desc())
        .limit(50)
        .all()
    )
    request_ids = [log.request_id for log in logs if log.request_id]
    request_map = {}
    if request_ids:
        requests = (
            db.query(models.ShiftRequest)
            .filter(models.ShiftRequest.id.in_(request_ids))
            .all()
        )
        request_map = {req.id: req for req in requests}

    entries: list[schemas.RequestFeedEntry] = []
    included_request_ids = set()
    for log in logs:
        req = request_map.get(log.request_id)
        if not req or req.id in included_request_ids:
            continue
        cancel_reason = None
        if log.action_type == "REQUEST_REOPEN":
            from_status = (log.details or {}).get("from_status")
            if from_status:
                cancel_reason = f"REOPEN_FROM_{from_status}"
        entries.append(
            _request_feed_entry(
                req,
                action_type=log.action_type,
                created_at=log.created_at,
                cancel_reason=cancel_reason,
            )
        )
        included_request_ids.add(req.id)

    recent_requests = (
        db.query(models.ShiftRequest)
        .order_by(models.ShiftRequest.created_at.desc())
        .limit(50)
        .all()
    )
    for req in recent_requests:
        if req.id in included_request_ids:
            continue
        entries.append(
            _request_feed_entry(
                req,
                action_type=_request_status_action_type(req),
                created_at=req.decided_at or req.created_at,
            )
        )
        included_request_ids.add(req.id)

    return sorted(entries, key=lambda entry: entry.created_at, reverse=True)[:50]


@router.get("/my/feed", response_model=list[schemas.RequestFeedEntry])
def my_request_feed(current=Depends(require_role(models.UserRole.MEMBER)), db: Session = Depends(get_db)):
    _expire_pending_requests(db, user_id=str(current.id))
    logs = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.action_type.in_(REQUEST_FEED_ACTIONS))
        .filter(models.AuditLog.request_id.isnot(None))
        .filter(models.AuditLog.target_user_id == current.id)
        .order_by(models.AuditLog.created_at.desc())
        .limit(50)
        .all()
    )
    request_ids = [log.request_id for log in logs if log.request_id]
    if not request_ids:
        return []
    requests = (
        db.query(models.ShiftRequest)
        .filter(models.ShiftRequest.id.in_(request_ids))
        .filter(models.ShiftRequest.user_id == current.id)
        .all()
    )
    request_map = {req.id: req for req in requests}

    entries: list[schemas.RequestFeedEntry] = []
    for log in logs:
        req = request_map.get(log.request_id)
        if not req:
            continue
        cancel_reason = None
        if log.action_type == "REQUEST_REOPEN":
            from_status = (log.details or {}).get("from_status")
            if from_status:
                cancel_reason = f"REOPEN_FROM_{from_status}"
        entries.append(
            _request_feed_entry(
                req,
                action_type=log.action_type,
                created_at=log.created_at,
                cancel_reason=cancel_reason,
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
    if req.status == models.RequestStatus.PENDING and _expire_pending_request(db, req, str(current.id)):
        db.commit()
        db.refresh(req)
        return req
    if req.status == models.RequestStatus.APPROVED and _is_past_target(req.target_date):
        raise HTTPException(status_code=400, detail=PAST_APPROVED_CANCEL_MESSAGE)

    was_approved = req.status == models.RequestStatus.APPROVED
    req.status = models.RequestStatus.CANCELLED
    req.cancel_reason = "USER_CANCEL"
    if was_approved:
        req.cancelled_after_approval = True
    req.decided_at = datetime.now(timezone.utc)
    req.operator_id = current.id
    db.commit()
    db.refresh(req)
    _record_request_cancel_log(db, req, str(current.id))
    db.commit()
    return req


@router.post("/{request_id}/reopen", response_model=schemas.RequestOut)
def reopen_request(request_id: str, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    req = db.query(models.ShiftRequest).filter(models.ShiftRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="신청 건을 찾을 수 없습니다")
    if req.status not in (models.RequestStatus.APPROVED, models.RequestStatus.REJECTED):
        raise HTTPException(status_code=400, detail="승인 또는 거절된 신청만 재검토 상태로 변경할 수 있습니다")
    if _is_past_target(req.target_date):
        raise HTTPException(status_code=400, detail=PAST_REOPEN_MESSAGE)

    from_status = req.status.value
    req.status = models.RequestStatus.PENDING
    req.cancel_reason = None
    req.cancelled_after_approval = False
    req.operator_id = None
    req.decided_at = None
    db.commit()
    db.refresh(req)
    _record_request_reopen_log(db, req, str(current.id), from_status)
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
    if req.status != models.RequestStatus.PENDING:
        raise HTTPException(status_code=400, detail="대기 중인 신청만 승인할 수 있습니다")
    if req.status == models.RequestStatus.PENDING and _expire_pending_request(db, req, str(current.id)):
        db.commit()
        raise HTTPException(status_code=400, detail=PAST_DECISION_MESSAGE)
    req.status = models.RequestStatus.APPROVED
    req.operator_id = current.id
    req.decided_at = datetime.now(timezone.utc)
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
    if req.status != models.RequestStatus.PENDING:
        raise HTTPException(status_code=400, detail="대기 중인 신청만 거절할 수 있습니다")
    if req.status == models.RequestStatus.PENDING and _expire_pending_request(db, req, str(current.id)):
        db.commit()
        raise HTTPException(status_code=400, detail=PAST_DECISION_MESSAGE)
    req.status = models.RequestStatus.REJECTED
    req.cancel_reason = "REJECTED"
    req.operator_id = current.id
    req.decided_at = datetime.now(timezone.utc)
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

