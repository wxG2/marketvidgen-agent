from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_key import ApiKey
from app.models.user import User

API_KEY_PREFIX = "vg_"
DEFAULT_API_KEY_SCOPES = ["video_jobs:create", "video_jobs:read", "video_jobs:review"]
ALLOWED_API_KEY_SCOPES = {*DEFAULT_API_KEY_SCOPES, "*"}


@dataclass(frozen=True)
class ApiKeyContext:
    user: User
    api_key: ApiKey


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def hash_idempotency_key(raw_key: str | None) -> str | None:
    value = (raw_key or "").strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_scopes(scopes: str | None) -> list[str]:
    if not scopes:
        return []
    return [item.strip() for item in scopes.split(",") if item.strip()]


def normalize_scopes(scopes: list[str] | None) -> list[str]:
    requested = scopes or DEFAULT_API_KEY_SCOPES
    normalized = []
    for scope in requested:
        item = (scope or "").strip()
        if not item:
            continue
        if item not in ALLOWED_API_KEY_SCOPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported API key scope: {item}")
        if item not in normalized:
            normalized.append(item)
    return normalized or list(DEFAULT_API_KEY_SCOPES)


def ensure_api_key_scope(context: ApiKeyContext, required_scope: str) -> None:
    scopes = set(parse_scopes(context.api_key.scopes))
    if "*" in scopes or required_scope in scopes:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"API key missing required scope: {required_scope}")


def serialize_api_key(record: ApiKey, *, raw_key: str | None = None) -> dict[str, Any]:
    payload = {
        "id": record.id,
        "user_id": record.user_id,
        "name": record.name,
        "key_prefix": record.key_prefix,
        "status": record.status,
        "scopes": parse_scopes(record.scopes),
        "last_used_at": record.last_used_at,
        "created_at": record.created_at,
    }
    if raw_key is not None:
        payload["api_key"] = raw_key
    return payload


async def create_api_key(
    db: AsyncSession,
    *,
    user_id: str,
    name: str,
    scopes: list[str] | None = None,
) -> tuple[ApiKey, str]:
    token = secrets.token_urlsafe(32)
    raw_key = f"{API_KEY_PREFIX}{token}"
    record = ApiKey(
        user_id=user_id,
        name=name.strip(),
        key_prefix=raw_key[:12],
        key_hash=hash_api_key(raw_key),
        status="active",
        scopes=",".join(normalize_scopes(scopes)),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record, raw_key


async def get_api_key_context(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyContext:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key header")
    raw_key = token.strip()
    if not raw_key.startswith(API_KEY_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    result = await db.execute(
        select(ApiKey, User)
        .join(User, User.id == ApiKey.user_id)
        .where(ApiKey.key_hash == hash_api_key(raw_key))
        .limit(1)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    api_key, user = row
    if api_key.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key is disabled")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    api_key.last_used_at = _utcnow()
    await db.commit()
    return ApiKeyContext(user=user, api_key=api_key)
