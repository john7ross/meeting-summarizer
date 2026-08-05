#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Authentication routes
"""
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from ...database.db import get_db
from ...database.models import User
from ...auth.auth_handler import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user
)
from ..schemas import UserRegister, UserLogin, Token, UserResponse

# Проверяем режим работы
SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'

if not SERVER_MODE:
    raise RuntimeError("auth routes should not be imported in desktop mode")

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_db)):
    """Регистрация нового пользователя"""

    # Проверяем существование username
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )

    # Проверяем существование email
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # The FIRST account on a fresh installation is the administrator: admin-only
    # operations (worker count, model downloads) were otherwise unreachable by
    # anyone, because every registration created a plain user and nothing in the
    # product could promote one.
    existing = (await db.execute(select(func.count()).select_from(User))).scalar_one()

    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=hashed_password,
        role="admin" if existing == 0 else "user",
        is_active=True
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return new_user


@router.post("/login", response_model=Token)
async def login(user_data: UserLogin, db: AsyncSession = Depends(get_db)):
    """Вход пользователя"""

    # Ищем пользователя
    result = await db.execute(select(User).where(User.username == user_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(user_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )

    # Обновляем last_login
    user.last_login = datetime.utcnow()
    await db.commit()

    # Создаем токен
    access_token = create_access_token(data={"sub": str(user.id)})  # JWT sub must be a string

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Получение информации о текущем пользователе"""
    return current_user


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """Выход пользователя (на клиенте нужно удалить токен)"""
    return {"message": "Successfully logged out"}
