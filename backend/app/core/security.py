# File: /backend/app/core/security.py
from datetime import datetime, timedelta
from typing import Optional

import jwt
from passlib.context import CryptContext

from ..config import get_settings
from ..models import UserRole

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def role_allows(user_role: UserRole, required: UserRole) -> bool:
    hierarchy = {UserRole.MEMBER: 1, UserRole.OPERATOR: 2, UserRole.MASTER: 3}
    return hierarchy[user_role] >= hierarchy[required]
