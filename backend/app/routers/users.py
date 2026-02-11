# File: /backend/app/routers/users.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from .. import schemas, models
from ..deps import get_db
from ..core.roles import require_role
from ..core.security import get_password_hash
from ..core.audit import record_log

router = APIRouter(prefix="/users", tags=["users"])


ROLE_ORDER = {models.UserRole.MEMBER: 1, models.UserRole.OPERATOR: 2, models.UserRole.MASTER: 3}


def _visible_users_query(db: Session, current: models.User):
    query = db.query(models.User).options(selectinload(models.User.auth_account))
    if current.role == models.UserRole.MASTER:
        return query
    if current.role == models.UserRole.OPERATOR:
        return query.filter(
            or_(
                models.User.role == models.UserRole.MEMBER,
                models.User.id == current.id,
            )
        )
    return query.filter(models.User.id == current.id)


def _assert_can_manage(current: models.User, target: models.User):
    if current.role == models.UserRole.MASTER:
        return
    if target.role == models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="Masters can only be managed by masters")
    if current.role == models.UserRole.OPERATOR and target.role == models.UserRole.OPERATOR and target.id != current.id:
        raise HTTPException(status_code=403, detail="Operators can only manage members or their own account")


@router.get("", response_model=list[schemas.UserOut])
def list_users(db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.MEMBER))):
    return _visible_users_query(db, current).all()


@router.post("", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: schemas.UserCreate, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    if current.role == models.UserRole.OPERATOR and payload.role != models.UserRole.MEMBER:
        raise HTTPException(status_code=403, detail="운영자는 구성원만 생성할 수 있습니다")

    conflict = db.query(models.AuthAccount).filter(models.AuthAccount.login_id == payload.login_id).first()
    if conflict:
        raise HTTPException(status_code=409, detail="이미 사용 중인 로그인 ID입니다")

    if payload.identifier:
        ident_conflict = db.query(models.User).filter(models.User.identifier == payload.identifier).first()
        if ident_conflict:
            raise HTTPException(status_code=409, detail="이미 등록된 개인 ID입니다")

    name_conflict = db.query(models.User).filter(models.User.name == payload.name).first()
    if name_conflict:
        raise HTTPException(status_code=409, detail="같은 이름의 사용자가 이미 존재합니다. 구분 가능한 이름 또는 개인 ID를 입력하세요")

    user = models.User(name=payload.name, identifier=payload.identifier, role=payload.role)
    db.add(user)
    db.flush()
    auth = models.AuthAccount(user_id=user.id, login_id=payload.login_id, password_hash=get_password_hash(payload.password))
    db.add(auth)
    db.commit()
    db.refresh(user)
    record_log(
        db,
        actor_id=str(current.id),
        action="USER_CREATE",
        target_user_id=str(user.id),
        details={"role": payload.role.value, "identifier": payload.identifier},
    )
    db.commit()
    return user


@router.get("/{user_id}", response_model=schemas.UserOut)
def get_user(user_id: str, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.MEMBER))):
    user = _visible_users_query(db, current).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    return user


@router.get("/{user_id}", response_model=schemas.UserOut)
def get_user(user_id: str, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.MEMBER))):
    user = _visible_users_query(db, current).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=schemas.UserOut)
def update_user(user_id: str, payload: schemas.UserUpdate, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    user = _visible_users_query(db, current).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    _assert_can_manage(current, user)

    if current.role == models.UserRole.OPERATOR and payload.role and payload.role != user.role:
        raise HTTPException(status_code=403, detail="운영자는 역할을 변경할 수 없습니다")
    if payload.role == models.UserRole.MASTER and current.role != models.UserRole.MASTER:
        raise HTTPException(status_code=403, detail="마스터만 마스터 역할을 부여할 수 있습니다")

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    record_log(
        db,
        actor_id=str(current.id),
        action="USER_UPDATE",
        target_user_id=str(user.id),
        details={"fields": list(payload.dict(exclude_unset=True).keys())},
    )
    db.commit()
    return user


@router.patch("/{user_id}/credentials", response_model=schemas.UserOut)
def update_credentials(user_id: str, payload: schemas.CredentialAdminUpdate, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    if not payload.new_login_id and not payload.new_password:
        raise HTTPException(status_code=400, detail="변경할 로그인 ID나 비밀번호를 입력하세요")

    user = _visible_users_query(db, current).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    _assert_can_manage(current, user)

    account = user.auth_account
    if not account:
        raise HTTPException(status_code=400, detail="로그인 계정이 없습니다")

    if payload.new_login_id:
        conflict = db.query(models.AuthAccount).filter(
            models.AuthAccount.login_id == payload.new_login_id,
            models.AuthAccount.user_id != user.id,
        ).first()
        if conflict:
            raise HTTPException(status_code=409, detail="이미 사용 중인 로그인 ID입니다")
        account.login_id = payload.new_login_id
    if payload.new_password:
        account.password_hash = get_password_hash(payload.new_password)

    db.add(account)
    db.commit()
    db.refresh(user)
    record_log(
        db,
        actor_id=str(current.id),
        action="CREDENTIAL_UPDATE",
        target_user_id=str(user.id),
        details={"login_id_changed": bool(payload.new_login_id), "password_changed": bool(payload.new_password)},
    )
    db.commit()
    return user


@router.patch("/{user_id}/credentials", response_model=schemas.UserOut)
def update_credentials(user_id: str, payload: schemas.CredentialAdminUpdate, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    if not payload.new_login_id and not payload.new_password:
        raise HTTPException(status_code=400, detail="No changes provided")

    user = _visible_users_query(db, current).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    _assert_can_manage(current, user)

    account = user.auth_account
    if not account:
        raise HTTPException(status_code=400, detail="User has no login account")

    if payload.new_login_id:
        conflict = db.query(models.AuthAccount).filter(
            models.AuthAccount.login_id == payload.new_login_id,
            models.AuthAccount.user_id != user.id,
        ).first()
        if conflict:
            raise HTTPException(status_code=409, detail="Login ID already exists")
        account.login_id = payload.new_login_id
    if payload.new_password:
        account.password_hash = get_password_hash(payload.new_password)

    db.add(account)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, db: Session = Depends(get_db), current=Depends(require_role(models.UserRole.OPERATOR))):
    user = _visible_users_query(db, current).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    _assert_can_manage(current, user)
    record_log(
        db,
        actor_id=str(current.id),
        action="USER_DELETE",
        target_user_id=str(user.id),
        details={"name": user.name},
    )
    db.delete(user)
    db.commit()
    return None
