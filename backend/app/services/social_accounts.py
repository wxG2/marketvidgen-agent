from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.social_account import SocialAccount


class DouyinOAuthConfigurationError(RuntimeError):
    """Raised when the server cannot start a valid Douyin OAuth flow."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _state_secret() -> str:
    return settings.DOUYIN_CLIENT_SECRET or "vidgen-douyin-state"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("utf-8"))


def _serialize_scopes(scopes: list[str] | str | None) -> str | None:
    if scopes is None:
        return None
    if isinstance(scopes, str):
        return scopes
    return ",".join(item for item in scopes if item)


def parse_scopes(scopes: str | None) -> list[str]:
    if not scopes:
        return []
    return [item.strip() for item in scopes.split(",") if item.strip()]


def validate_douyin_oauth_config() -> None:
    missing = [
        name
        for name, value in (
            ("DOUYIN_CLIENT_KEY", settings.DOUYIN_CLIENT_KEY),
            ("DOUYIN_CLIENT_SECRET", settings.DOUYIN_CLIENT_SECRET),
        )
        if not value
    ]
    if missing:
        raise DouyinOAuthConfigurationError(
            "抖音 OAuth 尚未配置完整：请在 .env 中填写 "
            f"{'、'.join(missing)}。抖音扫码授权页会由开放平台展示二维码，"
            "但生成扫码 URL 仍需要网站应用的 Client Key，扫码回调后换取 access_token 仍需要 Client Secret。"
        )

    redirect_uri = settings.DOUYIN_REDIRECT_URI.strip()
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "https" or not parsed.netloc:
        raise DouyinOAuthConfigurationError(
            "DOUYIN_REDIRECT_URI 必须是已在抖音开放平台网站应用中登记的 HTTPS 回调地址。"
            "抖音开放平台不会接受本地 http://127.0.0.1 回调；本地开发请使用公网 HTTPS 隧道，"
            "或配置部署环境的 HTTPS 回调地址。"
        )


def build_douyin_oauth_state(user_id: str) -> str:
    payload = {
        "user_id": user_id,
        "ts": int(_utcnow().timestamp()),
    }
    encoded = _b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    signature = hmac.new(_state_secret().encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_douyin_oauth_state(state: str, *, max_age_seconds: int = 900) -> dict[str, Any]:
    try:
        encoded, signature = state.split(".", 1)
    except ValueError as exc:
        raise RuntimeError("授权状态无效") from exc

    expected = hmac.new(_state_secret().encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise RuntimeError("授权状态校验失败")

    payload = json.loads(_b64decode(encoded).decode("utf-8"))
    ts = int(payload.get("ts") or 0)
    if ts <= 0 or (_utcnow() - datetime.fromtimestamp(ts, tz=timezone.utc)).total_seconds() > max_age_seconds:
        raise RuntimeError("授权已过期，请重新连接抖音账号")
    return payload


def build_douyin_authorization_url(user_id: str) -> str:
    validate_douyin_oauth_config()

    params = {
        "client_key": settings.DOUYIN_CLIENT_KEY,
        "response_type": "code",
        "scope": settings.DOUYIN_DEFAULT_SCOPE,
        "redirect_uri": settings.DOUYIN_REDIRECT_URI,
        "state": build_douyin_oauth_state(user_id),
    }
    return f"{settings.DOUYIN_OPEN_BASE_URL.rstrip('/')}/platform/oauth/connect/?{urlencode(params)}"


async def _exchange_token(payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.DOUYIN_OPEN_BASE_URL.rstrip('/')}/oauth/access_token/",
            data=payload,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"抖音授权换取 token 失败 ({response.status_code})：{response.text[:500]}")
        data = response.json()
    if data.get("error_code"):
        raise RuntimeError(data.get("description") or f"抖音授权失败：{data.get('error_code')}")
    return data.get("data") or data


async def exchange_douyin_code(code: str) -> dict[str, Any]:
    return await _exchange_token(
        {
            "client_key": settings.DOUYIN_CLIENT_KEY,
            "client_secret": settings.DOUYIN_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        }
    )


async def refresh_douyin_token(refresh_token: str) -> dict[str, Any]:
    return await _exchange_token(
        {
            "client_key": settings.DOUYIN_CLIENT_KEY,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    )


async def fetch_douyin_profile(access_token: str, open_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{settings.DOUYIN_OPEN_BASE_URL.rstrip('/')}/oauth/userinfo/",
            headers={"access-token": access_token},
            params={"open_id": open_id},
        )
        if response.status_code >= 400:
            return {}
        data = response.json()
    return (data.get("data") or {}).get("user") or data.get("data") or {}


async def upsert_douyin_social_account(
    db: AsyncSession,
    *,
    user_id: str,
    token_payload: dict[str, Any],
) -> SocialAccount:
    open_id = token_payload.get("open_id")
    access_token = token_payload.get("access_token")
    if not open_id or not access_token:
        raise RuntimeError("抖音授权结果缺少 open_id 或 access_token")

    result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.platform == "douyin", SocialAccount.open_id == open_id)
        .limit(1)
    )
    account = result.scalars().first()

    profile = {}
    try:
        profile = await fetch_douyin_profile(access_token, open_id)
    except Exception:
        profile = {}

    if account is None:
        has_default = (
            await db.execute(
                select(SocialAccount.id)
                .where(SocialAccount.user_id == user_id, SocialAccount.platform == "douyin", SocialAccount.is_default.is_(True))
                .limit(1)
            )
        ).first()
        account = SocialAccount(
            user_id=user_id,
            platform="douyin",
            open_id=open_id,
            access_token=access_token,
            is_default=not bool(has_default),
        )
        db.add(account)
    else:
        account.user_id = user_id
        account.access_token = access_token

    account.refresh_token = token_payload.get("refresh_token") or account.refresh_token
    expires_in = int(token_payload.get("expires_in") or 0)
    account.expires_at = _utcnow() + timedelta(seconds=expires_in) if expires_in > 0 else account.expires_at
    account.scopes = _serialize_scopes(token_payload.get("scope") or token_payload.get("scopes") or settings.DOUYIN_DEFAULT_SCOPE)
    account.status = "active"
    account.last_synced_at = _utcnow()
    account.display_name = profile.get("nickname") or profile.get("name") or account.display_name or f"抖音账号 {open_id[-6:]}"
    account.avatar_url = profile.get("avatar") or profile.get("avatar_url") or account.avatar_url

    await db.commit()
    await db.refresh(account)
    return account


async def ensure_active_douyin_account(db: AsyncSession, account: SocialAccount) -> SocialAccount:
    if account.platform != "douyin":
        raise RuntimeError("当前仅支持抖音账号发布")
    if account.status == "reauthorization_required":
        raise RuntimeError("抖音账号授权已失效，请重新连接账号")

    if account.expires_at and _ensure_utc(account.expires_at) <= _utcnow() + timedelta(minutes=5):
        if not account.refresh_token:
            account.status = "reauthorization_required"
            await db.commit()
            raise RuntimeError("抖音账号已过期且无法自动刷新，请重新连接账号")
        try:
            token_payload = await refresh_douyin_token(account.refresh_token)
            account = await upsert_douyin_social_account(
                db,
                user_id=account.user_id,
                token_payload={**token_payload, "open_id": token_payload.get("open_id") or account.open_id},
            )
        except Exception as exc:
            account.status = "reauthorization_required"
            await db.commit()
            raise RuntimeError(f"抖音账号刷新失败，请重新连接：{exc}") from exc
    return account


def serialize_social_account(account: SocialAccount) -> dict[str, Any]:
    return {
        "id": account.id,
        "user_id": account.user_id,
        "platform": account.platform,
        "open_id": account.open_id,
        "display_name": account.display_name,
        "avatar_url": account.avatar_url,
        "expires_at": account.expires_at,
        "scopes": parse_scopes(account.scopes),
        "status": account.status,
        "is_default": account.is_default,
        "last_synced_at": account.last_synced_at,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }
