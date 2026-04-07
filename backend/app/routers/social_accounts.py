from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_social_account_for_user
from app.database import get_db
from app.models.social_account import SocialAccount
from app.models.user import User
from app.schemas.social_account import SocialAccountConnectResponse, SocialAccountResponse
from app.services.social_accounts import (
    build_douyin_authorization_url,
    ensure_active_douyin_account,
    exchange_douyin_code,
    serialize_social_account,
    upsert_douyin_social_account,
    verify_douyin_oauth_state,
)

router = APIRouter(tags=["social-accounts"])


@router.get("/api/social-accounts", response_model=list[SocialAccountResponse])
async def list_social_accounts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.user_id == user.id)
        .order_by(SocialAccount.is_default.desc(), SocialAccount.updated_at.desc())
    )
    return [SocialAccountResponse(**serialize_social_account(item)) for item in result.scalars().all()]


@router.post("/api/social-accounts/douyin/connect", response_model=SocialAccountConnectResponse)
async def connect_douyin_social_account(
    user: User = Depends(get_current_user),
):
    return SocialAccountConnectResponse(authorization_url=build_douyin_authorization_url(user.id))


@router.get("/api/social-accounts/douyin/callback")
async def douyin_social_account_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    success = False
    message = "授权失败"
    try:
        if error:
            raise RuntimeError(error)
        if not code or not state:
            raise RuntimeError("缺少授权参数")
        state_payload = verify_douyin_oauth_state(state)
        user_id = state_payload.get("user_id")
        if not user_id:
            raise RuntimeError("授权状态缺少用户信息")
        token_payload = await exchange_douyin_code(code)
        account = await upsert_douyin_social_account(db, user_id=user_id, token_payload=token_payload)
        success = True
        message = f"抖音账号 {account.display_name or account.open_id[-6:]} 已连接"
    except Exception as exc:
        message = str(exc)

    html = f"""
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <title>抖音授权</title>
  </head>
  <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 24px;">
    <div>{message}</div>
    <script>
      if (window.opener) {{
        window.opener.postMessage({{
          type: 'vidgen-douyin-oauth',
          success: {str(success).lower()},
          message: {message!r}
        }}, '*');
        window.close();
      }}
    </script>
  </body>
</html>
"""
    return HTMLResponse(html)


@router.post("/api/social-accounts/{social_account_id}/refresh", response_model=SocialAccountResponse)
async def refresh_social_account(
    social_account_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account = await get_social_account_for_user(db, user.id, social_account_id)
    refreshed = await ensure_active_douyin_account(db, account)
    return SocialAccountResponse(**serialize_social_account(refreshed))


@router.patch("/api/social-accounts/{social_account_id}/default", response_model=SocialAccountResponse)
async def make_social_account_default(
    social_account_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account = await get_social_account_for_user(db, user.id, social_account_id)
    await db.execute(
        update(SocialAccount)
        .where(SocialAccount.user_id == user.id, SocialAccount.platform == account.platform)
        .values(is_default=False)
    )
    account.is_default = True
    await db.commit()
    await db.refresh(account)
    return SocialAccountResponse(**serialize_social_account(account))


@router.delete("/api/social-accounts/{social_account_id}")
async def delete_social_account(
    social_account_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account = await get_social_account_for_user(db, user.id, social_account_id)
    was_default = account.is_default
    platform = account.platform
    await db.delete(account)
    await db.commit()

    if was_default:
        result = await db.execute(
            select(SocialAccount)
            .where(SocialAccount.user_id == user.id, SocialAccount.platform == platform)
            .order_by(SocialAccount.updated_at.desc())
            .limit(1)
        )
        replacement = result.scalars().first()
        if replacement is not None:
            replacement.is_default = True
            await db.commit()

    return {"ok": True}
