# File: /backend/app/routers/notices.py
from datetime import datetime
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, exists
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import get_db
from ..core.roles import get_current_user, require_role
from ..core.audit import record_log

router = APIRouter(prefix="/notices", tags=["notices"])


NOTICE_TYPE_PERMISSIONS = {
    models.NoticeType.DB_MAINTENANCE: models.UserRole.MASTER,
    models.NoticeType.SYSTEM_MAINTENANCE: models.UserRole.MASTER,
    models.NoticeType.WORK_SPECIAL: models.UserRole.OPERATOR,
    models.NoticeType.GENERAL: models.UserRole.OPERATOR,
}


def _assert_notice_permission(current: models.User, notice_type: models.NoticeType) -> None:
    required = NOTICE_TYPE_PERMISSIONS.get(notice_type, models.UserRole.MASTER)
    order = {models.UserRole.MEMBER: 1, models.UserRole.OPERATOR: 2, models.UserRole.MASTER: 3}
    if order[current.role] < order[required]:
        raise HTTPException(status_code=403, detail="Insufficient permissions for notice type")
    if current.role == models.UserRole.OPERATOR and notice_type not in (
        models.NoticeType.WORK_SPECIAL,
        models.NoticeType.GENERAL,
    ):
        raise HTTPException(status_code=403, detail="Operators can only create work or general notices")


def _validate_targets(
    scope: models.NoticeScope,
    target_roles: Iterable[models.UserRole] | None,
    target_user_ids,
) -> None:
    if scope == models.NoticeScope.ROLE:
        if not target_roles:
            raise HTTPException(status_code=400, detail="target_roles required for ROLE scope")
        if target_user_ids:
            raise HTTPException(status_code=400, detail="target_user_ids not allowed for ROLE scope")
    elif scope == models.NoticeScope.USER:
        if not target_user_ids:
            raise HTTPException(status_code=400, detail="target_user_ids required for USER scope")
        if target_roles:
            raise HTTPException(status_code=400, detail="target_roles not allowed for USER scope")
    else:
        if target_roles or target_user_ids:
            raise HTTPException(status_code=400, detail="targets not allowed for ALL scope")


def _apply_scope_filter(query, current: models.User):
    user_match = exists().where(
        and_(models.NoticeTarget.notice_id == models.Notice.id, models.NoticeTarget.user_id == current.id)
    )
    return query.filter(
        or_(
            models.Notice.scope == models.NoticeScope.ALL,
            and_(
                models.Notice.scope == models.NoticeScope.ROLE,
                models.Notice.target_roles.isnot(None),
                models.Notice.target_roles.contains([current.role.value]),
            ),
            and_(models.Notice.scope == models.NoticeScope.USER, user_match),
        )
    )


def _apply_active_filter(query):
    now = datetime.utcnow()
    return query.filter(
        models.Notice.is_active.is_(True),
        or_(models.Notice.start_at.is_(None), models.Notice.start_at <= now),
        or_(models.Notice.end_at.is_(None), models.Notice.end_at >= now),
    )


def _notice_to_schema(notice: models.Notice, read: models.NoticeRead | None) -> schemas.NoticeListItem:
    target_user_ids = [target.user_id for target in notice.targets]
    target_roles = [models.UserRole(role) for role in notice.target_roles] if notice.target_roles else None
    return schemas.NoticeListItem(
        id=notice.id,
        title=notice.title,
        body=notice.body,
        type=notice.type,
        channel=notice.channel,
        scope=notice.scope,
        target_roles=target_roles,
        target_user_ids=target_user_ids or None,
        start_at=notice.start_at,
        end_at=notice.end_at,
        priority=notice.priority,
        is_active=notice.is_active,
        created_by=notice.created_by,
        creator_role=notice.creator.role if notice.creator else None,
        created_at=notice.created_at,
        updated_at=notice.updated_at,
        read_at=read.read_at if read else None,
        dismissed_at=read.dismissed_at if read else None,
    )


@router.get("", response_model=list[schemas.NoticeListItem])
def list_notices(
    channel: models.NoticeChannel | None = Query(default=None),
    unread_only: bool | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    include_all: bool = Query(default=False),
    db: Session = Depends(get_db),
    current: models.User = Depends(get_current_user),
):
    query = db.query(models.Notice)
    if channel == models.NoticeChannel.POPUP:
        query = query.filter(models.Notice.channel.in_([models.NoticeChannel.POPUP, models.NoticeChannel.POPUP_BANNER]))
    elif channel == models.NoticeChannel.BANNER:
        query = query.filter(models.Notice.channel.in_([models.NoticeChannel.BANNER, models.NoticeChannel.POPUP_BANNER]))
    elif channel == models.NoticeChannel.NONE:
        query = query.filter(models.Notice.channel == models.NoticeChannel.NONE)
    elif channel == models.NoticeChannel.BOARD:
        channel = models.NoticeChannel.BOARD
    if include_inactive or include_all:
        if current.role not in (models.UserRole.MASTER, models.UserRole.OPERATOR):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    if not include_inactive:
        query = _apply_active_filter(query)
    apply_scope = channel != models.NoticeChannel.BOARD
    if not include_all and apply_scope:
        query = _apply_scope_filter(query, current)

    read_alias = models.NoticeRead
    read_channel = channel if channel in (
        models.NoticeChannel.POPUP,
        models.NoticeChannel.BANNER,
        models.NoticeChannel.BOARD,
    ) else None
    channel_match = read_alias.channel == (read_channel or models.Notice.channel)
    query = query.outerjoin(
        read_alias,
        and_(
            read_alias.notice_id == models.Notice.id,
            read_alias.user_id == current.id,
            channel_match,
        ),
    )

    if channel == models.NoticeChannel.POPUP:
        cutoff = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(or_(read_alias.dismissed_at.is_(None), read_alias.dismissed_at < cutoff))
    elif channel == models.NoticeChannel.BANNER:
        unread_only = False
    if unread_only:
        query = query.filter(read_alias.dismissed_at.is_(None))

    notices = query.order_by(models.Notice.priority.desc(), models.Notice.created_at.desc()).limit(200).all()
    items: list[schemas.NoticeListItem] = []
    for notice in notices:
        read = None
        for read_entry in notice.reads:
            if read_entry.user_id == current.id and read_entry.channel == notice.channel:
                read = read_entry
                break
        items.append(_notice_to_schema(notice, read))
    return items


@router.get("/{notice_id}", response_model=schemas.NoticeOut)
def get_notice(
    notice_id: str,
    db: Session = Depends(get_db),
    current: models.User = Depends(get_current_user),
):
    notice = db.query(models.Notice).filter(models.Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="notice_not_found")
    if notice.scope != models.NoticeScope.ALL:
        filtered = _apply_scope_filter(db.query(models.Notice).filter(models.Notice.id == notice_id), current).first()
        if not filtered:
            raise HTTPException(status_code=403, detail="not_allowed")
    target_user_ids = [target.user_id for target in notice.targets]
    target_roles = [models.UserRole(role) for role in notice.target_roles] if notice.target_roles else None
    return schemas.NoticeOut(
        id=notice.id,
        title=notice.title,
        body=notice.body,
        type=notice.type,
        channel=notice.channel,
        scope=notice.scope,
        target_roles=target_roles,
        target_user_ids=target_user_ids or None,
        start_at=notice.start_at,
        end_at=notice.end_at,
        priority=notice.priority,
        is_active=notice.is_active,
        created_by=notice.created_by,
        creator_role=notice.creator.role if notice.creator else None,
        created_at=notice.created_at,
        updated_at=notice.updated_at,
    )


@router.post("", response_model=schemas.NoticeOut, status_code=status.HTTP_201_CREATED)
def create_notice(
    payload: schemas.NoticeCreate,
    db: Session = Depends(get_db),
    current: models.User = Depends(require_role(models.UserRole.OPERATOR)),
):
    _assert_notice_permission(current, payload.type)
    _validate_targets(payload.scope, payload.target_roles, payload.target_user_ids)

    notice = models.Notice(
        title=payload.title,
        body=payload.body,
        type=payload.type,
        channel=payload.channel,
        scope=payload.scope,
        target_roles=[role.value for role in payload.target_roles] if payload.target_roles else None,
        priority=payload.priority,
        is_active=payload.is_active,
        start_at=payload.start_at,
        end_at=payload.end_at,
        created_by=current.id,
    )
    db.add(notice)
    db.flush()
    if payload.scope == models.NoticeScope.USER and payload.target_user_ids:
        for user_id in payload.target_user_ids:
            db.add(models.NoticeTarget(notice_id=notice.id, user_id=user_id))

    record_log(
        db,
        actor_id=str(current.id),
        action="NOTICE_CREATE",
        details={"notice_id": str(notice.id), "channel": notice.channel.value},
    )
    db.commit()
    db.refresh(notice)
    target_user_ids = [target.user_id for target in notice.targets]
    target_roles = [models.UserRole(role) for role in notice.target_roles] if notice.target_roles else None
    return schemas.NoticeOut(
        id=notice.id,
        title=notice.title,
        body=notice.body,
        type=notice.type,
        channel=notice.channel,
        scope=notice.scope,
        target_roles=target_roles,
        target_user_ids=target_user_ids or None,
        start_at=notice.start_at,
        end_at=notice.end_at,
        priority=notice.priority,
        is_active=notice.is_active,
        created_by=notice.created_by,
        creator_role=notice.creator.role if notice.creator else None,
        created_at=notice.created_at,
        updated_at=notice.updated_at,
    )


@router.patch("/{notice_id}", response_model=schemas.NoticeOut)
def update_notice(
    notice_id: str,
    payload: schemas.NoticeUpdate,
    db: Session = Depends(get_db),
    current: models.User = Depends(require_role(models.UserRole.OPERATOR)),
):
    notice = db.query(models.Notice).filter(models.Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="notice_not_found")
    if current.role == models.UserRole.OPERATOR and notice.creator and notice.creator.role == models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Only master can edit master notices")

    new_type = payload.type or notice.type
    _assert_notice_permission(current, new_type)
    new_scope = payload.scope or notice.scope
    current_roles = [models.UserRole(role) for role in notice.target_roles] if notice.target_roles else None
    current_users = [t.user_id for t in notice.targets]
    roles_for_validation = payload.target_roles if new_scope == models.NoticeScope.ROLE else None
    if roles_for_validation is None and new_scope == models.NoticeScope.ROLE:
        roles_for_validation = current_roles
    users_for_validation = payload.target_user_ids if new_scope == models.NoticeScope.USER else None
    if users_for_validation is None and new_scope == models.NoticeScope.USER:
        users_for_validation = current_users
    _validate_targets(new_scope, roles_for_validation, users_for_validation)

    for field in ["title", "body", "type", "channel", "scope", "start_at", "end_at", "priority", "is_active"]:
        value = getattr(payload, field)
        if value is not None:
            setattr(notice, field, value)

    if new_scope != models.NoticeScope.ROLE:
        notice.target_roles = None
    elif payload.target_roles is not None:
        notice.target_roles = [role.value for role in payload.target_roles] if payload.target_roles else None

    if new_scope != models.NoticeScope.USER:
        db.query(models.NoticeTarget).filter(models.NoticeTarget.notice_id == notice.id).delete()
    elif payload.target_user_ids is not None:
        db.query(models.NoticeTarget).filter(models.NoticeTarget.notice_id == notice.id).delete()
        for user_id in payload.target_user_ids:
            db.add(models.NoticeTarget(notice_id=notice.id, user_id=user_id))


    record_log(
        db,
        actor_id=str(current.id),
        action="NOTICE_UPDATE",
        details={"notice_id": str(notice.id)},
    )
    db.commit()
    db.refresh(notice)
    target_user_ids = [target.user_id for target in notice.targets]
    target_roles = [models.UserRole(role) for role in notice.target_roles] if notice.target_roles else None
    return schemas.NoticeOut(
        id=notice.id,
        title=notice.title,
        body=notice.body,
        type=notice.type,
        channel=notice.channel,
        scope=notice.scope,
        target_roles=target_roles,
        target_user_ids=target_user_ids or None,
        start_at=notice.start_at,
        end_at=notice.end_at,
        priority=notice.priority,
        is_active=notice.is_active,
        created_by=notice.created_by,
        creator_role=notice.creator.role if notice.creator else None,
        created_at=notice.created_at,
        updated_at=notice.updated_at,
    )


@router.delete("/{notice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notice(
    notice_id: str,
    db: Session = Depends(get_db),
    current: models.User = Depends(require_role(models.UserRole.OPERATOR)),
):
    notice = db.query(models.Notice).filter(models.Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="notice_not_found")
    if current.role == models.UserRole.OPERATOR and notice.creator and notice.creator.role == models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Only master can delete master notices")
    record_log(
        db,
        actor_id=str(current.id),
        action="NOTICE_DELETE",
        details={"notice_id": str(notice.id)},
    )
    db.delete(notice)
    db.commit()
    return None


@router.post("/{notice_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_notice_read(
    notice_id: str,
    payload: schemas.NoticeReadAction,
    db: Session = Depends(get_db),
    current: models.User = Depends(get_current_user),
):
    notice = db.query(models.Notice).filter(models.Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="notice_not_found")
    read = (
        db.query(models.NoticeRead)
        .filter(
            models.NoticeRead.notice_id == notice_id,
            models.NoticeRead.user_id == current.id,
            models.NoticeRead.channel == payload.channel,
        )
        .first()
    )
    if not read:
        read = models.NoticeRead(notice_id=notice_id, user_id=current.id, channel=payload.channel)
        db.add(read)
    if not read.read_at:
        read.read_at = datetime.utcnow()
    db.commit()
    return None


@router.post("/{notice_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_notice(
    notice_id: str,
    payload: schemas.NoticeReadAction,
    db: Session = Depends(get_db),
    current: models.User = Depends(get_current_user),
):
    notice = db.query(models.Notice).filter(models.Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="notice_not_found")
    read = (
        db.query(models.NoticeRead)
        .filter(
            models.NoticeRead.notice_id == notice_id,
            models.NoticeRead.user_id == current.id,
            models.NoticeRead.channel == payload.channel,
        )
        .first()
    )
    if not read:
        read = models.NoticeRead(notice_id=notice_id, user_id=current.id, channel=payload.channel)
        db.add(read)
    if not read.read_at:
        read.read_at = datetime.utcnow()
    read.dismissed_at = datetime.utcnow()
    db.commit()
    return None
