from datetime import date, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from .. import models, schemas


def _valid_for_date(assignment: models.UserShift, target: date) -> bool:
    if assignment.valid_from > target:
        return False
    if assignment.valid_to and assignment.valid_to < target:
        return False
    return True


def week_events(db: Session, start: date, user_filter: str | None = None) -> list[schemas.ScheduleEvent]:
    """기존 배정과 승인된 요청(결근/추가)을 합친 주간 이벤트를 계산."""
    end = start + timedelta(days=6)
    assignments = db.query(models.UserShift).join(models.Shift).join(models.User)
    if user_filter:
        assignments = assignments.filter(models.UserShift.user_id == user_filter)
    assignments = assignments.all()

    requests = db.query(models.ShiftRequest).filter(
        models.ShiftRequest.status == models.RequestStatus.APPROVED,
        models.ShiftRequest.target_date >= start,
        models.ShiftRequest.target_date <= end,
    )
    if user_filter:
        requests = requests.filter(models.ShiftRequest.user_id == user_filter)
    requests = requests.all()

    users = {u.id: u for u in db.query(models.User).all()}
    shifts = {s.id: s for s in db.query(models.Shift).all()}

    base_events: list[schemas.ScheduleEvent] = []
    for offset in range(7):
        current_date = start + timedelta(days=offset)
        weekday = current_date.weekday()
        for a in assignments:
            if not _valid_for_date(a, current_date):
                continue
            if a.shift.weekday != weekday:
                continue
            base_events.append(
                schemas.ScheduleEvent(
                    user_id=a.user_id,
                    user_name=a.user.name,
                    role=a.user.role,
                    date=current_date,
                    start_time=a.shift.start_time,
                    end_time=a.shift.end_time,
                    shift_id=a.shift_id,
                    shift_name=a.shift.name,
                    location=a.shift.location,
                    valid_from=a.valid_from,
                    valid_to=a.valid_to,
                    source="BASE",
                )
            )

    absences_by_key: dict[tuple[str, date, str], list[models.ShiftRequest]] = {}
    for req in requests:
        if req.type != models.RequestType.ABSENCE:
            continue
        absences_by_key.setdefault((str(req.user_id), req.target_date, str(req.target_shift_id)), []).append(req)

    filtered_events: list[schemas.ScheduleEvent] = []
    for ev in base_events:
        key = (str(ev.user_id), ev.date, str(ev.shift_id))
        abs_list = absences_by_key.get(key, [])
        segments = [ev]
        for abs_req in abs_list:
            abs_start = abs_req.target_start_time or ev.start_time
            abs_end = abs_req.target_end_time or ev.end_time
            new_segments: list[schemas.ScheduleEvent] = []
            for seg in segments:
                if abs_end <= seg.start_time or abs_start >= seg.end_time:
                    new_segments.append(seg)
                    continue
                if abs_start > seg.start_time:
                    new_segments.append(
                        schemas.ScheduleEvent(
                            user_id=seg.user_id,
                            user_name=seg.user_name,
                            role=seg.role,
                            date=seg.date,
                            start_time=seg.start_time,
                            end_time=abs_start,
                            shift_id=seg.shift_id,
                            shift_name=seg.shift_name,
                            location=seg.location,
                            source=seg.source,
                        )
                    )
                if abs_end < seg.end_time:
                    new_segments.append(
                        schemas.ScheduleEvent(
                            user_id=seg.user_id,
                            user_name=seg.user_name,
                            role=seg.role,
                            date=seg.date,
                            start_time=abs_end,
                            end_time=seg.end_time,
                            shift_id=seg.shift_id,
                            shift_name=seg.shift_name,
                            location=seg.location,
                            source=seg.source,
                        )
                    )
            segments = new_segments
        filtered_events.extend(segments)

    # extras add
    extras: list[schemas.ScheduleEvent] = []
    for req in requests:
        if req.type != models.RequestType.EXTRA:
            continue
        shift = shifts.get(req.target_shift_id)
        user = users.get(req.user_id)
        if not shift or not user:
            continue
        start_time = req.target_start_time or shift.start_time
        end_time = req.target_end_time or shift.end_time
        extras.append(
            schemas.ScheduleEvent(
                user_id=user.id,
                user_name=user.name,
                role=user.role,
                date=req.target_date,
                start_time=start_time,
                end_time=end_time,
                shift_id=shift.id,
                shift_name=shift.name,
                location=shift.location,
                valid_from=None,
                valid_to=None,
                source="EXTRA",
            )
        )

    return filtered_events + extras


def week_base_events(db: Session, start: date, user_filter: str | None = None) -> list[schemas.ScheduleEvent]:
    """고정 배정만 반영한 주간 이벤트를 계산."""
    assignments = db.query(models.UserShift).join(models.Shift).join(models.User)
    if user_filter:
        assignments = assignments.filter(models.UserShift.user_id == user_filter)
    assignments = assignments.all()

    base_events: list[schemas.ScheduleEvent] = []
    for offset in range(7):
        current_date = start + timedelta(days=offset)
        weekday = current_date.weekday()
        for a in assignments:
            if not _valid_for_date(a, current_date):
                continue
            if a.shift.weekday != weekday:
                continue
            base_events.append(
                schemas.ScheduleEvent(
                    user_id=a.user_id,
                    user_name=a.user.name,
                    role=a.user.role,
                    date=current_date,
                    start_time=a.shift.start_time,
                    end_time=a.shift.end_time,
                    shift_id=a.shift_id,
                    shift_name=a.shift.name,
                    location=a.shift.location,
                    valid_from=a.valid_from,
                    valid_to=a.valid_to,
                    source="BASE",
                )
            )
    return base_events
