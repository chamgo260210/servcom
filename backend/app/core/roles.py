# File: /backend/app/core/roles.py
from fastapi import HTTPException, status, Depends
from fastapi.security import OAuth2PasswordBearer
import jwt
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import User, UserRole
from ..deps import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
settings = get_settings()


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        session_version = payload.get("session_version")
        if user_id is None or session_version is None:
            raise credentials_exception
        token_session_version = int(session_version)
    except (jwt.PyJWTError, TypeError, ValueError):
        raise credentials_exception
    user = db.query(User).filter(User.id == user_id, User.active == True).first()
    if not user or not user.auth_account:
        raise credentials_exception
    if int(user.auth_account.session_version) != token_session_version:
        raise credentials_exception
    return user


def require_role(min_role: UserRole):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        order = {UserRole.MEMBER: 1, UserRole.OPERATOR: 2, UserRole.MASTER: 3}
        if order[current_user.role] < order[min_role]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return role_checker
