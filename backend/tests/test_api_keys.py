from __future__ import annotations

import pytest
import uuid
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.models.user import User


@pytest.mark.asyncio
async def test_current_user_can_create_list_and_disable_api_key(client: AsyncClient):
    create_response = await client.post("/api/api-keys", json={"name": "integration"})
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["api_key"].startswith("vg_")
    assert created["status"] == "active"
    assert "video_jobs:review" in created["scopes"]

    list_response = await client.get("/api/api-keys")
    assert list_response.status_code == 200
    listed = list_response.json()
    assert any(item["id"] == created["id"] for item in listed)
    assert all("api_key" not in item for item in listed)

    disable_response = await client.post(f"/api/api-keys/{created['id']}/disable")
    assert disable_response.status_code == 200
    assert disable_response.json()["status"] == "disabled"


@pytest.mark.asyncio
async def test_admin_can_create_and_disable_api_key_for_user(client: AsyncClient, db: AsyncSession):
    user = User(username=f"managed-user-{uuid.uuid4().hex}", password_hash=hash_password("password123"), role="user", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    create_response = await client.post(
        "/api/admin/api-keys",
        json={"user_id": user.id, "name": "managed", "scopes": ["video_jobs:read"]},
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["user_id"] == user.id
    assert created["api_key"].startswith("vg_")
    assert created["scopes"] == ["video_jobs:read"]

    list_response = await client.get("/api/admin/api-keys")
    assert list_response.status_code == 200
    assert any(item["id"] == created["id"] and item["user_id"] == user.id for item in list_response.json())

    disable_response = await client.post(f"/api/admin/api-keys/{created['id']}/disable")
    assert disable_response.status_code == 200
    assert disable_response.json()["status"] == "disabled"
