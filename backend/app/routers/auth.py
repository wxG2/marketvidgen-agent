from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    adopt_orphaned_records,
    clear_session_cookie,
    count_users,
    create_user_session,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.database import get_db
from app.models.user import User, UserSession
from app.schemas.auth import AdminUserUpdateRequest, AuthUserResponse, LoginRequest, RegisterRequest

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/register", response_model=AuthUserResponse)
async def register(data: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.username == data.username).limit(1))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")

    is_first_user = await count_users(db) == 0
    user = User(
        username=data.username.strip(),
        password_hash=hash_password(data.password),
        role="admin" if is_first_user else "user",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    if is_first_user:
        await adopt_orphaned_records(db, user.id)

    await create_user_session(db, user, response)
    return user


@router.post("/auth/login", response_model=AuthUserResponse)
async def login(data: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == data.username).limit(1))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    await create_user_session(db, user, response)
    return user


@router.post("/auth/logout")
async def logout(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserSession).where(UserSession.user_id == user.id))
    for session in result.scalars().all():
        await db.delete(session)
    await db.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/auth/me", response_model=AuthUserResponse)
async def me(user: User = Depends(get_current_user)):
    return user


@router.get("/admin/users", response_model=list[AuthUserResponse])
async def list_users(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.created_at.asc()))
    return list(result.scalars().all())


@router.patch("/admin/users/{user_id}", response_model=AuthUserResponse)
async def update_user(
    user_id: str,
    data: AdminUserUpdateRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password:
        user.password_hash = hash_password(data.password)
    await db.commit()
    await db.refresh(user)
    return user
