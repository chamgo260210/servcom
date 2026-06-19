# File: /backend/app/routers/history.py
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from .. import models, schemas
from ..deps import get_db
from ..core.roles import require_role

router = APIRouter(prefix="/history", tags=["history"])


DEFAULT_HISTORY_DAYS = 30
DEFAULT_HISTORY_LIMIT = 50
ALLOWED_HISTORY_DAYS = {"30", "90", "all"}
ALLOWED_HISTORY_LIMITS = {50, 100, 200, 500}
DEFAULT_EXPORT_LIMIT = 500
ALLOWED_EXPORT_LIMITS = {100, 500, 1000, 5000}
ALLOWED_EXPORT_FORMATS = {"csv", "json"}

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

REQUEST_TYPE_LABEL = {
    "ABSENCE": "휴무 신청",
    "EXTRA": "추가 근무 신청",
}

DETAIL_SUMMARY_KEYS = [
    "scope",
    "type",
    "date",
    "target_date",
    "visit_date",
    "academic_year",
    "publication_id",
    "title",
    "layout_id",
    "shelf_id",
    "code",
    "operation",
    "cancel_reason",
]


def _parse_limit(value: str | int | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_HISTORY_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_HISTORY_LIMIT
    return parsed if parsed in ALLOWED_HISTORY_LIMITS else DEFAULT_HISTORY_LIMIT


def _parse_export_limit(value: str | int | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_EXPORT_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_EXPORT_LIMIT
    return parsed if parsed in ALLOWED_EXPORT_LIMITS else DEFAULT_EXPORT_LIMIT


def _parse_export_format(value: str | None) -> str:
    normalized = str(value or "csv").strip().lower()
    return normalized if normalized in ALLOWED_EXPORT_FORMATS else "csv"


def _parse_days(value: str | None) -> str:
    normalized = str(value or DEFAULT_HISTORY_DAYS).strip().lower()
    return normalized if normalized in ALLOWED_HISTORY_DAYS else str(DEFAULT_HISTORY_DAYS)


def _history_cutoff(days: str) -> datetime | None:
    if days == "all":
        return None
    return datetime.now(timezone.utc) - timedelta(days=int(days))


def _age_days(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max((now - value).days, 0)


def _age_minutes(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max(int((now - value).total_seconds() // 60), 0)


def _as_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _detail_value(details: dict, *keys: str) -> str | None:
    for key in keys:
        value = _as_text(details.get(key))
        if value:
            return value
    return None


def _snapshot_value(details: dict, snapshot_key: str) -> str | None:
    snapshot = details.get(snapshot_key)
    if not isinstance(snapshot, dict):
        return None
    return _detail_value(snapshot, "name", "display_name", "username", "identifier", "login_id")


def _user_name(user: models.User | None) -> str | None:
    if not user:
        return None
    return _as_text(user.name) or _as_text(user.identifier)


def _actor_display(log: models.AuditLog, users: dict) -> str:
    details = log.details if isinstance(log.details, dict) else {}
    linked_name = _user_name(users.get(log.actor_user_id))
    return (
        linked_name
        or _snapshot_value(details, "actor_snapshot")
        or _detail_value(details, "actor_name", "actor_username", "performed_by", "created_by", "updated_by")
        or "알 수 없음"
    )


def _target_display(log: models.AuditLog, users: dict) -> str:
    details = log.details if isinstance(log.details, dict) else {}
    linked_name = _user_name(users.get(log.target_user_id))
    return (
        linked_name
        or _snapshot_value(details, "target_snapshot")
        or _snapshot_value(details, "user_snapshot")
        or _detail_value(details, "target_name", "target_username", "user_name", "member_name", "target_identifier")
        or "-"
    )


def _format_request_type(value) -> str | None:
    text = _as_text(getattr(value, "value", value))
    if not text:
        return None
    return REQUEST_TYPE_LABEL.get(text, text)


def _format_request_from_model(req: models.ShiftRequest | None) -> str | None:
    if not req:
        return None
    parts = [
        req.target_date.isoformat() if req.target_date else None,
        _format_request_type(req.type),
    ]
    if req.target_shift and req.target_shift.name:
        parts.append(req.target_shift.name)
    elif req.target_start_time and req.target_end_time:
        parts.append(f"{req.target_start_time.strftime('%H:%M')}~{req.target_end_time.strftime('%H:%M')}")
    return " / ".join(part for part in parts if part) or None


def _format_request_from_snapshot(snapshot: dict) -> str | None:
    parts = [
        _detail_value(snapshot, "target_date", "date"),
        _format_request_type(snapshot.get("type")),
        _detail_value(snapshot, "shift_name", "target_shift_name", "shift", "target_shift_id"),
    ]
    if not parts[2]:
        start_time = _detail_value(snapshot, "target_start_time", "start_time")
        end_time = _detail_value(snapshot, "target_end_time", "end_time")
        if start_time and end_time:
            parts[2] = f"{start_time}~{end_time}"
    return " / ".join(part for part in parts if part) or None


def _request_display(log: models.AuditLog, requests: dict) -> str:
    details = log.details if isinstance(log.details, dict) else {}
    linked_text = _format_request_from_model(requests.get(log.request_id))
    if linked_text:
        return linked_text
    snapshot = details.get("request_snapshot")
    if isinstance(snapshot, dict):
        snapshot_text = _format_request_from_snapshot(snapshot)
        if snapshot_text:
            return snapshot_text
    detail_text = _format_request_from_snapshot(details)
    if detail_text:
        return detail_text
    return "-"


def _details_summary(details: dict | None) -> str | None:
    if not isinstance(details, dict):
        return None
    parts = []
    for key in DETAIL_SUMMARY_KEYS:
        value = details.get(key)
        if value is None:
            continue
        if key == "type":
            value = _format_request_type(value)
        text = _as_text(value)
        if text:
            parts.append(f"{key}: {text}")
    return " / ".join(parts) if parts else None


def _entry_from_log(log: models.AuditLog, users: dict, requests: dict) -> schemas.HistoryEntry:
    actor_name = _user_name(users.get(log.actor_user_id))
    target_name = _user_name(users.get(log.target_user_id))
    return schemas.HistoryEntry(
        id=log.id,
        action_type=log.action_type,
        action_label=ACTION_LABEL.get(log.action_type, log.action_type),
        actor_user_id=log.actor_user_id,
        actor_name=actor_name,
        actor_display_name=_actor_display(log, users),
        target_user_id=log.target_user_id,
        target_name=target_name,
        target_display_name=_target_display(log, users),
        request_id=log.request_id,
        request_display_text=_request_display(log, requests),
        details_summary=_details_summary(log.details),
        details=log.details,
        created_at=log.created_at,
    )


def _request_map_for_logs(db: Session, logs: list[models.AuditLog]) -> dict:
    request_ids = [log.request_id for log in logs if log.request_id]
    if not request_ids:
        return {}
    requests = (
        db.query(models.ShiftRequest)
        .filter(models.ShiftRequest.id.in_(request_ids))
        .all()
    )
    return {request.id: request for request in requests}


def _history_query(db: Session, days: str, action_type: str | None):
    cutoff = _history_cutoff(days)
    query = db.query(models.AuditLog)
    if cutoff is not None:
        query = query.filter(models.AuditLog.created_at >= cutoff)
    if action_type and action_type.strip():
        query = query.filter(models.AuditLog.action_type == action_type.strip())
    return query


def _csv_safe(value) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _history_entry_to_export_row(entry: schemas.HistoryEntry) -> dict[str, str]:
    details = json.dumps(entry.details, ensure_ascii=False, default=str) if entry.details else ""
    return {
        "created_at": _csv_safe(entry.created_at.isoformat()),
        "action_type": _csv_safe(entry.action_type),
        "action_label": _csv_safe(entry.action_label),
        "actor_user_id": _csv_safe(entry.actor_user_id),
        "actor_name": _csv_safe(entry.actor_name),
        "actor_display_name": _csv_safe(entry.actor_display_name),
        "target_user_id": _csv_safe(entry.target_user_id),
        "target_name": _csv_safe(entry.target_name),
        "target_display_name": _csv_safe(entry.target_display_name),
        "request_id": _csv_safe(entry.request_id),
        "request_display_text": _csv_safe(entry.request_display_text),
        "details_summary": _csv_safe(entry.details_summary),
        "details": _csv_safe(details),
    }


@router.get("/stats", response_model=schemas.HistoryStatsOut)
def history_stats(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.MASTER))):
    now = datetime.now(timezone.utc)
    recent_7_cutoff = now - timedelta(days=7)
    recent_cutoff = now - timedelta(days=DEFAULT_HISTORY_DAYS)
    recent_90_cutoff = now - timedelta(days=90)
    total_logs = db.query(func.count(models.AuditLog.id)).scalar() or 0
    logs_last_7_days = (
        db.query(func.count(models.AuditLog.id))
        .filter(models.AuditLog.created_at >= recent_7_cutoff)
        .scalar()
        or 0
    )
    recent_30_days = (
        db.query(func.count(models.AuditLog.id))
        .filter(models.AuditLog.created_at >= recent_cutoff)
        .scalar()
        or 0
    )
    logs_last_90_days = (
        db.query(func.count(models.AuditLog.id))
        .filter(models.AuditLog.created_at >= recent_90_cutoff)
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
        logs_last_7_days=logs_last_7_days,
        recent_30_days=recent_30_days,
        logs_last_90_days=logs_last_90_days,
        display_limit=DEFAULT_HISTORY_LIMIT,
        current_window_days=DEFAULT_HISTORY_DAYS,
        oldest_log=oldest_log,
        newest_log=newest_log,
        oldest_log_age_days=_age_days(oldest_log, now),
        newest_log_age_minutes=_age_minutes(newest_log, now),
        request_linked=request_linked,
        request_unlinked=total_logs - request_linked,
        actor_linked=actor_linked,
        actor_missing=total_logs - actor_linked,
        action_type_count=len(action_counts),
        orphan_request_logs=None,
        orphan_actor_logs=None,
        orphan_target_logs=None,
        by_action={action_type: count for action_type, count in action_counts},
    )


def _audit_orphan_counts(db: Session) -> tuple[int, int, int]:
    actor_user = aliased(models.User)
    target_user = aliased(models.User)
    orphan_request_logs = (
        db.query(func.count(models.AuditLog.id))
        .outerjoin(models.ShiftRequest, models.AuditLog.request_id == models.ShiftRequest.id)
        .filter(models.AuditLog.request_id.isnot(None), models.ShiftRequest.id.is_(None))
        .scalar()
        or 0
    )
    orphan_actor_logs = (
        db.query(func.count(models.AuditLog.id))
        .outerjoin(actor_user, models.AuditLog.actor_user_id == actor_user.id)
        .filter(models.AuditLog.actor_user_id.isnot(None), actor_user.id.is_(None))
        .scalar()
        or 0
    )
    orphan_target_logs = (
        db.query(func.count(models.AuditLog.id))
        .outerjoin(target_user, models.AuditLog.target_user_id == target_user.id)
        .filter(models.AuditLog.target_user_id.isnot(None), target_user.id.is_(None))
        .scalar()
        or 0
    )
    return orphan_request_logs, orphan_actor_logs, orphan_target_logs


@router.get("/diagnostics", response_model=schemas.HistoryDiagnosticsOut)
def history_diagnostics(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.MASTER))):
    orphan_request_logs, orphan_actor_logs, orphan_target_logs = _audit_orphan_counts(db)
    return schemas.HistoryDiagnosticsOut(
        orphan_request_logs=orphan_request_logs,
        orphan_actor_logs=orphan_actor_logs,
        orphan_target_logs=orphan_target_logs,
        checked_at=datetime.now(timezone.utc),
    )


@router.get("/export")
def history_export(
    days: str | None = None,
    limit: str | None = None,
    action_type: str | None = None,
    format: str | None = None,
    db: Session = Depends(get_db),
    current=Depends(require_role(models.UserRole.MASTER)),
):
    selected_days = _parse_days(days)
    selected_limit = _parse_export_limit(limit)
    selected_format = _parse_export_format(format)
    logs = (
        _history_query(db, selected_days, action_type)
        .order_by(models.AuditLog.created_at.desc())
        .limit(selected_limit)
        .all()
    )
    users = {u.id: u for u in db.query(models.User).all()}
    requests = _request_map_for_logs(db, logs)
    entries = [_entry_from_log(log, users, requests) for log in logs]
    if selected_format == "json":
        return JSONResponse(content=[entry.model_dump(mode="json") for entry in entries])

    output = io.StringIO()
    fieldnames = [
        "created_at",
        "action_type",
        "action_label",
        "actor_user_id",
        "actor_name",
        "actor_display_name",
        "target_user_id",
        "target_name",
        "target_display_name",
        "request_id",
        "request_display_text",
        "details_summary",
        "details",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for entry in entries:
        writer.writerow(_history_entry_to_export_row(entry))
    filename = f"audit_history_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(
        content=f"\ufeff{output.getvalue()}",
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    query = _history_query(db, selected_days, action_type)
    if current.role == models.UserRole.MEMBER:
        query = query.filter((models.AuditLog.actor_user_id == current.id) | (models.AuditLog.target_user_id == current.id))
    logs = query.order_by(models.AuditLog.created_at.desc()).limit(selected_limit).all()
    users = {u.id: u for u in db.query(models.User).all()}
    requests = _request_map_for_logs(db, logs)
    return [_entry_from_log(log, users, requests) for log in logs]
