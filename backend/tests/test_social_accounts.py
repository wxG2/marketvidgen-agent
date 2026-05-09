from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.social_accounts import ensure_active_douyin_account


async def test_connect_douyin_requires_client_credentials(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "DOUYIN_CLIENT_KEY", "")
    monkeypatch.setattr(settings, "DOUYIN_CLIENT_SECRET", "")
    monkeypatch.setattr(settings, "DOUYIN_REDIRECT_URI", "https://vidgen.example.com/api/social-accounts/douyin/callback")

    response = await client.post("/api/social-accounts/douyin/connect")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "DOUYIN_CLIENT_KEY" in detail
    assert "DOUYIN_CLIENT_SECRET" in detail
    assert "扫码" in detail


async def test_connect_douyin_requires_https_redirect_uri(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "DOUYIN_CLIENT_KEY", "test-client-key")
    monkeypatch.setattr(settings, "DOUYIN_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "DOUYIN_REDIRECT_URI", "http://127.0.0.1:8000/api/social-accounts/douyin/callback")

    response = await client.post("/api/social-accounts/douyin/connect")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "DOUYIN_REDIRECT_URI" in detail
    assert "HTTPS" in detail
    assert "127.0.0.1" in detail


async def test_connect_douyin_returns_authorization_url(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "DOUYIN_CLIENT_KEY", "test-client-key")
    monkeypatch.setattr(settings, "DOUYIN_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "DOUYIN_REDIRECT_URI", "https://vidgen.example.com/api/social-accounts/douyin/callback")
    monkeypatch.setattr(settings, "DOUYIN_DEFAULT_SCOPE", "user_info,video.create")

    response = await client.post("/api/social-accounts/douyin/connect")

    assert response.status_code == 200
    authorization_url = response.json()["authorization_url"]
    parsed = urlparse(authorization_url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "open.douyin.com"
    assert parsed.path == "/platform/oauth/connect/"
    assert params["client_key"] == ["test-client-key"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["user_info,video.create"]
    assert params["redirect_uri"] == ["https://vidgen.example.com/api/social-accounts/douyin/callback"]
    assert params["state"][0]


async def test_expired_douyin_account_without_refresh_token_marks_reauthorization_required(db: AsyncSession):
    user = User(username="douyin_user", password_hash="hash", role="user", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    account = SocialAccount(
        user_id=user.id,
        platform="douyin",
        open_id="open_id_123",
        access_token="expired",
        refresh_token=None,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        status="active",
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    try:
        await ensure_active_douyin_account(db, account)
    except RuntimeError as exc:
        assert "重新连接" in str(exc)
    else:
        raise AssertionError("Expected expired account to require reauthorization")

    await db.refresh(account)
    assert account.status == "reauthorization_required"
