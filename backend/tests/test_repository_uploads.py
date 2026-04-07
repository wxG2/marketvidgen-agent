from __future__ import annotations

import os
import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auto_chat import AutoChatSession
from app.models.video_upload import VideoUpload


async def test_upload_video_rejects_duplicate_filename(client: AsyncClient, db: AsyncSession):
    project_resp = await client.post("/api/projects", json={"name": f"Upload Dup {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]
    filename = f"dup-{uuid.uuid4().hex}.mp4"

    first = await client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": (filename, b"video-bytes-1", "video/mp4")},
    )
    assert first.status_code == 200

    duplicate = await client.post(
        f"/api/projects/{project_id}/upload",
        files={"file": (filename, b"video-bytes-2", "video/mp4")},
    )
    assert duplicate.status_code == 409
    assert "已存在于仓库中" in duplicate.json()["detail"]

    uploads = (
        await db.execute(select(VideoUpload).where(VideoUpload.filename == filename))
    ).scalars().all()
    assert len(uploads) == 1


async def test_delete_repository_upload_removes_file_and_clears_session_reference(
    client: AsyncClient,
    db: AsyncSession,
):
    project_resp = await client.post("/api/projects", json={"name": f"Delete Upload {uuid.uuid4().hex[:8]}"})
    project_id = project_resp.json()["id"]

    session_resp = await client.post(f"/api/projects/{project_id}/auto-sessions")
    session_id = session_resp.json()["session"]["id"]

    filename = f"delete-{uuid.uuid4().hex}.mp4"
    upload_resp = await client.post(
        f"/api/projects/{project_id}/upload",
        data={"session_id": session_id},
        files={"file": (filename, b"video-delete-me", "video/mp4")},
    )
    assert upload_resp.status_code == 200
    upload_id = upload_resp.json()["id"]

    upload = await db.get(VideoUpload, upload_id)
    assert upload is not None
    upload_file_path = upload.file_path
    assert os.path.exists(upload_file_path)

    session = await db.get(AutoChatSession, session_id)
    assert session is not None
    assert session.reference_video_id == upload_id

    delete_resp = await client.delete(f"/api/repository/uploads/{upload_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"ok": True}

    db.expire_all()
    assert await db.get(VideoUpload, upload_id) is None

    cleared_session = await db.get(AutoChatSession, session_id)
    assert cleared_session is not None
    assert cleared_session.reference_video_id is None

    assert not os.path.exists(upload_file_path)
