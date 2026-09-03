#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JWT Authentication handler
ВАЖНО: Используется ТОЛЬКО в серверном режиме
"""
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..database.db import get_db
from ..database.models import User

# Проверяем режим работы
SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'

if not SERVER_MODE:
    raise RuntimeError("auth_handler should not be imported in desktop mode")

# JWT настройки

def _load_secret_key() -> str:
    """The JWT signing key. Never fall back to a hardcoded public default (that
    would let anyone forge tokens for any user). Priority:
      1. ``JWT_SECRET_KEY`` env var (recommended for deployment);
      2. a random secret persisted to ``config/.jwt_secret`` (stable across restarts);
      3. an ephemeral random secret (last resort; invalidates tokens on restart)."""
    env = os.getenv("JWT_SECRET_KEY")
    if env:
        return env
    f = Path(__file__).resolve().parents[2] / "config" / ".jwt_secret"
    try:
        if f.exists():
            saved = f.read_text(encoding="utf-8").strip()
            if saved:
                return saved
        f.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(48)
        f.write_text(generated, encoding="utf-8")
        print("WARNING: JWT_SECRET_KEY not set; generated a random secret at "
              "config/.jwt_secret. Set JWT_SECRET_KEY in production.")
        return generated
    except Exception:  # noqa: BLE001
        return secrets.token_urlsafe(48)


SECRET_KEY = _load_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 дней

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTP Bearer для токенов
security = HTTPBearer()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверка пароля"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Хеширование пароля"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Создание JWT токена"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> dict:
    """Декодирование JWT токена"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Получение текущего пользователя из токена"""
    token = credentials.credentials

    # Декодируем токен
    payload = decode_access_token(token)
    sub = payload.get("sub")

    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = int(sub)   # sub is stored as a string per the JWT spec
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Получаем пользователя из БД
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )

    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Проверка что пользователь активен"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


async def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Проверка что пользователь - администратор"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user
