from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models.api_key import ApiKey
from app.models.user import User
from app.schemas.public_api import (
    AdminApiKeyCreateRequest,
    AdminApiKeyCreateResponse,
    AdminApiKeyResponse,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyResponse,
)
from app.services.api_keys import create_api_key, serialize_api_key

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])
admin_router = APIRouter(prefix="/api/admin/api-keys", tags=["api-keys"])


@router.post("", response_model=ApiKeyCreateResponse)
async def create_current_user_api_key(
    data: ApiKeyCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    record, raw_key = await create_api_key(db, user_id=user.id, name=data.name, scopes=data.scopes)
    return ApiKeyCreateResponse(**serialize_api_key(record, raw_key=raw_key))


@router.get("", response_model=list[ApiKeyResponse])
async def list_current_user_api_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    )
    return [ApiKeyResponse(**serialize_api_key(record)) for record in result.scalars().all()]


@router.post("/{api_key_id}/disable", response_model=ApiKeyResponse)
async def disable_current_user_api_key(
    api_key_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(ApiKey, api_key_id)
    if not record or record.user_id != user.id:
        raise HTTPException(status_code=404, detail="API key not found")
    record.status = "disabled"
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(record)
    return ApiKeyResponse(**serialize_api_key(record))


@admin_router.get("", response_model=list[AdminApiKeyResponse])
async def list_all_api_keys(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return [AdminApiKeyResponse(**serialize_api_key(record)) for record in result.scalars().all()]


@admin_router.post("", response_model=AdminApiKeyCreateResponse)
async def create_api_key_for_user(
    data: AdminApiKeyCreateRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    record, raw_key = await create_api_key(db, user_id=user.id, name=data.name, scopes=data.scopes)
    return AdminApiKeyCreateResponse(**serialize_api_key(record, raw_key=raw_key))


@admin_router.post("/{api_key_id}/disable", response_model=AdminApiKeyResponse)
async def disable_any_api_key(
    api_key_id: str,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(ApiKey, api_key_id)
    if not record:
        raise HTTPException(status_code=404, detail="API key not found")
    record.status = "disabled"
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(record)
    return AdminApiKeyResponse(**serialize_api_key(record))
